"""The server's tools, like the desktop app's (``gui/tools.py``): each adds its pages and a tab.

A new tool is a module with a ``TOOL = WebTool(...)`` and is listed in :data:`ALL`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class WebTool:
    id: str
    title: str
    path: str  # the tab's link
    router: Callable  # (ServerContext) -> APIRouter
    min_role: str = "viewer"  # who sees the tab
    start: Callable | None = None  # (ServerContext) -> None, run once at startup
    stop: Callable | None = None  # (ServerContext) -> None, run at shutdown


def all_tools() -> list[WebTool]:
    from . import admin, fetch, qc
    return [qc.TOOL, fetch.TOOL, admin.TOOL]
