"""Admin pages: users, server folders, lab defaults (added by the QC tool) and the audit log."""

from __future__ import annotations

import csv
import io
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from .. import audit, paths
from ..auth import ROLE_HELP, ROLES, AuthError, Session
from ..web import ServerContext, render, require
from . import WebTool

ADMIN_PAGES = (("/admin/users", "Users"), ("/admin/folders", "Folders"), ("/admin/defaults", "Lab defaults"),
               ("/admin/audit", "Audit log"))


def temporary_password(username: str = "") -> str:
    """Easy to read out or type once (the user must change it at first sign-in)."""
    from ..auth import password_problem
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    while True:
        password = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
        if not password_problem(password, username):  # e.g. a short user name inside the random text
            return password


def _users_page(request: Request, ctx: ServerContext, **extra):
    return render(request, "admin_users.html", users=ctx.accounts.users(), roles=ROLES, role_help=ROLE_HELP,
                  admin_pages=ADMIN_PAGES, **extra)


def router(ctx: ServerContext) -> APIRouter:
    r = APIRouter(prefix="/admin")
    admin = require("admin")

    @r.get("")
    def admin_home():
        return RedirectResponse("/admin/users", status_code=303)

    # --- users -------------------------------------------------------------------------------------------

    @r.get("/users")
    def users(request: Request, sess: Session = Depends(admin)):
        return _users_page(request, ctx, error="", shown_password=None)

    @r.post("/users")
    def add_user(request: Request, username: str = Form(""), display_name: str = Form(""), role: str = Form("analyst"),
                 sess: Session = Depends(admin)):
        password = temporary_password(username)
        try:
            user = ctx.accounts.create(username, display_name, role, password, must_change=True)
        except AuthError as exc:
            return _users_page(request, ctx, error=str(exc), shown_password=None)
        audit.record(ctx.db, sess.user, "user.created", user.username, role=role)
        return _users_page(request, ctx, error="", shown_password=(user.username, password))

    @r.post("/users/{user_id}")
    def edit_user(request: Request, user_id: int, display_name: str = Form(""), role: str = Form(""),
                  active: str = Form(""), sess: Session = Depends(admin)):
        before = ctx.accounts.get(user_id)
        if before is None:
            raise HTTPException(404, "No such user.")
        try:
            after = ctx.accounts.update(user_id, display_name=display_name or None, role=role or None,
                                        active=active == "1")
        except AuthError as exc:
            return _users_page(request, ctx, error=str(exc), shown_password=None)
        changes = {k: [getattr(before, k), getattr(after, k)] for k in ("display_name", "role", "active")
                   if getattr(before, k) != getattr(after, k)}
        if changes:
            audit.record(ctx.db, sess.user, "user.changed", after.username, **changes)
        return RedirectResponse("/admin/users", status_code=303)

    @r.post("/users/{user_id}/reset")
    def reset_password(request: Request, user_id: int, sess: Session = Depends(admin)):
        user = ctx.accounts.get(user_id)
        if user is None:
            raise HTTPException(404, "No such user.")
        password = temporary_password(user.username)
        ctx.accounts.set_password(user_id, password, must_change=True)
        audit.record(ctx.db, sess.user, "user.password_reset", user.username)
        return _users_page(request, ctx, error="", shown_password=(user.username, password))

    # --- folders -----------------------------------------------------------------------------------------

    def folders_page(request: Request, **extra):
        return render(request, "admin_folders.html", roots=paths.roots(ctx.db), admin_pages=ADMIN_PAGES,
                      checks=extra.pop("checks", {}), error=extra.pop("error", ""), **extra)

    @r.get("/folders")
    def folders(request: Request, sess: Session = Depends(admin)):
        return folders_page(request)

    @r.post("/folders")
    def add_folder(request: Request, name: str = Form(""), path: str = Form(""), writable: str = Form(""),
                   sess: Session = Depends(admin)):
        name, path = name.strip(), path.strip()
        if not name or not path:
            return folders_page(request, error="Give the folder a name and a path.")
        from pathlib import Path
        if not Path(path).is_absolute():
            return folders_page(request, error="Use a full path, e.g. \\\\fileserver\\Measles or /mnt/measles.")
        items = paths.roots(ctx.db)
        item = paths.Root(paths.new_root_id(), name, path, writable == "1")
        items.append(item)
        paths.save_roots(ctx.db, items)
        audit.record(ctx.db, sess.user, "folder.added", name, path=path, writable=item.writable)
        return folders_page(request, checks={item.id: paths.check_access(item)})

    @r.post("/folders/{root_id}/delete")
    def remove_folder(request: Request, root_id: str, sess: Session = Depends(admin)):
        items = paths.roots(ctx.db)
        gone = [x for x in items if x.id == root_id]
        paths.save_roots(ctx.db, [x for x in items if x.id != root_id])
        if gone:
            audit.record(ctx.db, sess.user, "folder.removed", gone[0].name, path=gone[0].path)
        return RedirectResponse("/admin/folders", status_code=303)

    @r.post("/folders/{root_id}/check")
    def check_folder(request: Request, root_id: str, sess: Session = Depends(admin)):
        try:
            item = paths.root(ctx.db, root_id)
        except paths.PathError as exc:
            raise HTTPException(404, str(exc)) from None
        return folders_page(request, checks={item.id: paths.check_access(item)})

    # --- audit log ---------------------------------------------------------------------------------------

    @r.get("/audit")
    def audit_log(request: Request, action: str = "", user: str = "", q: str = "", sess: Session = Depends(admin)):
        rows = audit.entries(ctx.db, action, user, q)
        intact, checked, problem = audit.verify(ctx.db)
        return render(request, "admin_audit.html", rows=rows, action=action, who=user, q=q, intact=intact,
                      checked=checked, problem=problem, admin_pages=ADMIN_PAGES)

    @r.get("/audit.csv")
    def audit_csv(request: Request, action: str = "", user: str = "", q: str = "", sess: Session = Depends(admin)):
        rows = audit.entries(ctx.db, action, user, q, limit=1_000_000)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["time (UTC)", "user", "action", "target", "details"])
        for row in rows:
            w.writerow([row["at"], row["username"], row["action"], row["target"], row["details"]])
        audit.record(ctx.db, sess.user, "audit.exported", rows=len(rows))
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})

    return r


TOOL = WebTool("admin", "Admin", "/admin", router, min_role="admin")
