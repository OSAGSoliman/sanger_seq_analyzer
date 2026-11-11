"""Tool registry: the window hosts one or more tools, each contributing pages to the header tabs.

This mirrors ``cli.build_parser``: a new analysis tool adds a :class:`ToolSpec`
in its own package and the shell shows it without changes to the QC tool (SH-5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget


class ToolUI(QObject):
    """What a tool gives the shell: pages, actions for the menus, and state signals."""

    contextTextChanged = Signal(str)  # text at the right of the header
    pageEnabledChanged = Signal(str, bool)  # page key, enabled
    pageRequested = Signal(str)  # switch to this page

    def pages(self) -> list[tuple[str, str, QWidget]]:
        """(key, tab title, widget) in tab order."""
        raise NotImplementedError

    def actions(self) -> dict[str, QAction]:
        """Named actions the shell places in its menus (e.g. "add_folder", "run", "export")."""
        return {}

    def can_close(self) -> bool:
        """Return False to keep the window open (e.g. the user declined to cancel a run)."""
        return True

    def save_state(self) -> None:
        """Persist anything worth remembering when the window closes."""


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    factory: Callable[..., ToolUI]  # factory(window, store) -> ToolUI


def available_tools() -> list[ToolSpec]:
    from .qc import SPEC as QC

    return [QC]
