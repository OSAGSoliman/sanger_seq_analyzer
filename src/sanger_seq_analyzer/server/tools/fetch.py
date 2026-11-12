"""Fetch data: link the genotyping table to the lab files and save MeaNS rows (milestone 4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import Session
from ..web import ServerContext, render, require
from . import WebTool


def router(ctx: ServerContext) -> APIRouter:
    r = APIRouter()

    @r.get("/fetch")
    def fetch_home(request: Request, sess: Session = Depends(require("analyst"))):
        return render(request, "placeholder.html", title="Fetch data")

    return r


TOOL = WebTool("fetch", "Fetch data", "/fetch", router, min_role="analyst")
