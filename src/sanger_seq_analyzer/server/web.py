"""What every page needs: the server context, the signed-in user, CSRF checks and rendering.

Route functions that call the analysis core are plain ``def`` (FastAPI runs them in a
thread pool), so long work never blocks the server's event loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import __version__
from .auth import ROLE_NAMES, Accounts, Session
from .config import Config
from .db import Database, parse_time

log = logging.getLogger("sanger_seq_analyzer.server")
HERE = Path(__file__).resolve().parent
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class NeedLogin(Exception):
    pass


class MustChangePassword(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message: str = "You do not have permission to do this."):
        super().__init__(message)
        self.message = message


@dataclass
class ServerContext:
    config: Config
    db: Database
    accounts: Accounts
    templates: Jinja2Templates
    tz: ZoneInfo
    tools: list = field(default_factory=list)
    services: dict = field(default_factory=dict)  # per-tool shared objects (job queue, source cache…)

    def local(self, stamp: str | datetime | None) -> datetime | None:
        when = parse_time(stamp) if isinstance(stamp, str) else stamp
        return when.astimezone(self.tz) if when else None

    def today(self):
        return datetime.now(self.tz).date()


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown time zone %r, using UTC", name)
        return ZoneInfo("UTC")


def ctx_of(request: Request) -> ServerContext:
    return request.app.state.ctx


def _session(request: Request) -> Session | None:
    if not hasattr(request.state, "session"):
        ctx = ctx_of(request)
        from .auth import COOKIE
        request.state.session = ctx.accounts.session(request.cookies.get(COOKIE))
    return request.state.session


def _same_site(request: Request, url: str) -> bool:
    expected = ctx_of(request).config.public_url
    origin = urlsplit(url)
    if expected:
        want = urlsplit(expected)
        return (origin.scheme, origin.netloc) == (want.scheme, want.netloc)
    return origin.netloc == request.headers.get("host", "")


async def _check_csrf(request: Request, sess: Session) -> None:
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and origin != "null" and not _same_site(request, origin):
        raise Forbidden("This request came from another web site and was refused.")
    token = request.headers.get("x-csrf-token")
    if token is None:
        form = await request.form()
        token = form.get("csrf")
    if not token or token != sess.csrf:
        raise Forbidden("The page was out of date. Reload it and try again.")


def require(role: str = "viewer", allow_must_change: bool = False):
    """Dependency: the signed-in session with at least ``role``; checks CSRF on changes."""
    async def dependency(request: Request) -> Session:
        sess = _session(request)
        if sess is None:
            raise NeedLogin()
        if sess.user.must_change and not allow_must_change:
            raise MustChangePassword()
        if not sess.user.can(role):
            raise Forbidden(f"This needs the {ROLE_NAMES[role]} role. Ask an admin.")
        await _check_csrf(request, sess)
        return sess
    return dependency


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def login_url(request: Request) -> str:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return f"/login?next={quote(target)}"


def render(request: Request, template: str, status_code: int = 200, **values) -> HTMLResponse:
    ctx = ctx_of(request)
    sess = getattr(request.state, "session", None)
    values.setdefault("user", sess.user if sess else None)
    values.setdefault("csrf", sess.csrf if sess else "")
    values.setdefault("tools", [t for t in ctx.tools if sess and sess.user.can(t.min_role)])
    values.setdefault("version", __version__)
    values.setdefault("path", request.url.path)
    values.setdefault("https", ctx.config.https)
    return ctx.templates.TemplateResponse(request, template, values, status_code=status_code)


def make_templates(ctx_tz: ZoneInfo) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    env = templates.env
    env.autoescape = True

    def local_time(stamp, fmt="%d %b %Y %H:%M"):
        when = parse_time(stamp) if isinstance(stamp, str) else stamp
        return when.astimezone(ctx_tz).strftime(fmt) if when else ""

    env.filters["local"] = local_time
    env.globals["role_names"] = ROLE_NAMES
    return templates
