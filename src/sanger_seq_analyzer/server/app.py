"""The FastAPI application: pages, security headers, error pages and the tools' routers."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from . import account, assets
from .auth import Accounts
from .config import Config
from .db import Database
from .tools import all_tools
from .web import (
    HERE, SAFE_METHODS, Forbidden, MustChangePassword, NeedLogin, ServerContext, _same_site, is_htmx, login_url,
    make_templates, render, zone,
)

log = logging.getLogger("sanger_seq_analyzer.server")

# Pages may only load scripts and styles from this server. The sample viewer (in a sandboxed
# frame) sets its own, stricter policy.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'none'; "
       "object-src 'none'")


def create_app(config: Config, executor=None) -> FastAPI:
    """Build the app for ``config``. ``executor`` runs QC jobs (tests pass one that runs them inline)."""
    config.make_dirs()
    db = Database(config.db_path, backups=config.backups_dir)
    db.migrate()
    tz = zone(config.timezone)
    ctx = ServerContext(config, db, Accounts(db, config.session_idle_minutes, config.session_max_hours),
                        make_templates(tz), tz, tools=all_tools())
    if executor is not None:
        ctx.services["executor"] = executor

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for tool in ctx.tools:
            if tool.start:
                tool.start(ctx)
        yield
        for tool in reversed(ctx.tools):
            if tool.stop:
                tool.stop(ctx)

    app = FastAPI(title="Sanger Seq Analyzer", version=__version__, lifespan=lifespan, docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    @app.middleware("http")
    async def security(request: Request, call_next):
        # requests that change data must come from this site (the CSRF token is checked as well)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin != "null" and not _same_site(request, origin):
                return PlainTextResponse("Refused: this request came from another web site.", status_code=403)
        response: Response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        headers.setdefault("Cache-Control", "public, max-age=3600" if request.url.path.startswith("/static/")
                           else "no-store")
        if config.https:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(NeedLogin)
    async def need_login(request: Request, exc: NeedLogin):
        if is_htmx(request):
            return Response(status_code=401, headers={"HX-Redirect": login_url(request)})
        if request.method not in SAFE_METHODS:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse(login_url(request), status_code=303)

    @app.exception_handler(MustChangePassword)
    async def must_change(request: Request, exc: MustChangePassword):
        if is_htmx(request):
            return Response(status_code=403, headers={"HX-Redirect": "/account/password"})
        return RedirectResponse("/account/password", status_code=303)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden):
        if is_htmx(request):
            return HTMLResponse(f'<div class="notice error" role="alert">{_escape(exc.message)}</div>',
                                status_code=403, headers={"HX-Retarget": "#flash", "HX-Reswap": "innerHTML"})
        return render(request, "error.html", status_code=403, title="Not allowed", message=exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
        if exc.status_code == 404 and message == "Not Found":
            message = "This page does not exist (or was removed)."
        if is_htmx(request):
            return HTMLResponse(f'<div class="notice error" role="alert">{_escape(message)}</div>',
                                status_code=exc.status_code,
                                headers={"HX-Retarget": "#flash", "HX-Reswap": "innerHTML"})
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": message}, status_code=exc.status_code)
        return render(request, "error.html", status_code=exc.status_code, title=f"Error {exc.status_code}",
                      message=message)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        db.version()
        return {"status": "ok", "version": __version__}

    @app.get("/static/theme.css", include_in_schema=False)
    def theme_css():
        return Response(assets.theme_css(), media_type="text/css",
                        headers={"Cache-Control": "public, max-age=3600"})

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(account.router())
    for tool in ctx.tools:
        app.include_router(tool.router(ctx))

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        return RedirectResponse(ctx.tools[0].path, status_code=303)

    return app


def _escape(text: str) -> str:
    import html
    return html.escape(str(text))
