"""QC runs: upload .ab1 files, run the analysis, review and export (filled in by milestone 2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import Session
from ..web import ServerContext, render, require
from . import WebTool


def router(ctx: ServerContext) -> APIRouter:
    r = APIRouter()

    @r.get("/runs")
    def runs(request: Request, sess: Session = Depends(require("viewer"))):
        return render(request, "placeholder.html", title="Runs")

    return r


TOOL = WebTool("qc", "Runs", "/runs", router)
