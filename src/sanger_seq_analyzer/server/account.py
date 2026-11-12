"""Sign in, sign out and change password."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from . import audit
from .auth import COOKIE, AuthError, Session
from .web import ctx_of, render, require


def _safe_next(target: str | None) -> str:
    # only paths on this site: never "//other.site" or "https://…"
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def router() -> APIRouter:
    r = APIRouter()

    @r.get("/login")
    def login_page(request: Request, next: str = "/"):
        ctx = ctx_of(request)
        first = ctx.accounts.count_admins() == 0
        return render(request, "login.html", next=_safe_next(next), error="", first_run=first, username="")

    @r.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
        ctx = ctx_of(request)
        try:
            token, user = ctx.accounts.sign_in(username, password)
        except AuthError as exc:
            audit.record(ctx.db, None, "login.failed", username[:40])
            return render(request, "login.html", status_code=401, next=_safe_next(next), error=str(exc),
                          first_run=False, username=username)
        audit.record(ctx.db, user, "login")
        response = RedirectResponse(_safe_next(next), status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=ctx.config.https, path="/",
                            max_age=ctx.config.session_max_hours * 3600)
        return response

    @r.post("/logout")
    def logout(request: Request, sess: Session = Depends(require("viewer", allow_must_change=True))):
        ctx = ctx_of(request)
        ctx.accounts.sign_out(request.cookies.get(COOKIE))
        audit.record(ctx.db, sess.user, "logout")
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @r.get("/account/password")
    def password_page(request: Request, sess: Session = Depends(require("viewer", allow_must_change=True))):
        return render(request, "password.html", error="", done=False)

    @r.post("/account/password")
    def change_password(request: Request, current: str = Form(""), new: str = Form(""), again: str = Form(""),
                        sess: Session = Depends(require("viewer", allow_must_change=True))):
        ctx = ctx_of(request)
        if new != again:
            return render(request, "password.html", status_code=400, error="The two new passwords differ.",
                          done=False)
        try:
            ctx.accounts.change_own_password(sess.user, current, new)
        except AuthError as exc:
            return render(request, "password.html", status_code=400, error=str(exc), done=False)
        audit.record(ctx.db, sess.user, "password.changed")
        # changing the password ends every session of this user: sign in again with the new one
        response = RedirectResponse("/login?changed=1", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    return r
