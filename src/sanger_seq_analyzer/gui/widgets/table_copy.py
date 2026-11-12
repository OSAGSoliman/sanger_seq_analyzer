"""Copy from tables as tab-separated text, so it pastes into Excel cell by cell.

Qt's own Ctrl+C in a table copies only the current cell. ``install_copy`` makes Ctrl+C
copy every selected cell, and adds a right-click menu: Copy, Copy with headers, and
Copy all shown rows. Hidden rows (a filter) and hidden columns are left out.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QMenu


def _clean(value) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split()) if any(ch in text for ch in "\t\r\n") else text


def _visible(view: QAbstractItemView, skip_columns=()) -> tuple[list[int], list[int]]:
    model = view.model()
    rows = [r for r in range(model.rowCount()) if not view.isRowHidden(r)]
    header = view.horizontalHeader()
    cols = [c for c in range(model.columnCount()) if not view.isColumnHidden(c) and c not in skip_columns]
    cols.sort(key=header.visualIndex)  # the order the user sees
    return rows, cols


def table_text(view: QAbstractItemView, *, selected_only: bool = True, headers: bool = False,
               skip_columns=()) -> str:
    """The selected cells (or every shown cell) as tab-separated lines."""
    model = view.model()
    rows, cols = _visible(view, skip_columns)
    if selected_only:
        chosen = {(i.row(), i.column()) for i in view.selectionModel().selectedIndexes()}
        if not chosen and view.currentIndex().isValid():
            chosen = {(view.currentIndex().row(), view.currentIndex().column())}
        rows = [r for r in rows if any((r, c) in chosen for c in cols)]
        cols = [c for c in cols if any((r, c) in chosen for r in rows)]
    else:
        chosen = None
    lines = []
    if headers and cols:
        lines.append("\t".join(_clean(model.headerData(c, Qt.Orientation.Horizontal)) for c in cols))
    for r in rows:
        lines.append("\t".join(_clean(model.index(r, c).data()) if chosen is None or (r, c) in chosen else ""
                               for c in cols))
    return "\n".join(lines) + ("\n" if lines else "")


class _CopyKeys(QObject):
    """Event filter: Ctrl+C (Cmd+C on a Mac) copies the selection instead of only the current cell."""

    def __init__(self, view: QAbstractItemView, skip_columns):
        super().__init__(view)
        self.view, self.skip = view, tuple(skip_columns)

    def eventFilter(self, obj, e) -> bool:
        if e.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and e.matches(QKeySequence.StandardKey.Copy):
            if e.type() == QEvent.Type.KeyPress:
                copy_selection(self.view, skip_columns=self.skip)
            e.accept()
            return True
        return False


def copy_selection(view: QAbstractItemView, *, headers: bool = False, skip_columns=()) -> str:
    text = table_text(view, headers=headers, skip_columns=skip_columns)
    if text:
        QGuiApplication.clipboard().setText(text)
    return text


def copy_all(view: QAbstractItemView, *, skip_columns=()) -> str:
    text = table_text(view, selected_only=False, headers=True, skip_columns=skip_columns)
    QGuiApplication.clipboard().setText(text)
    return text


def copy_menu(view: QAbstractItemView, pos, skip_columns=(), extra=None) -> QMenu:
    """The right-click menu; right-clicking a cell outside the selection selects it first, as Excel does."""
    index = view.indexAt(pos)
    if index.isValid() and not view.selectionModel().isSelected(index):
        view.setCurrentIndex(index)
    menu = QMenu(view)
    has = bool(view.selectionModel().selectedIndexes())
    act = menu.addAction("Copy", lambda: copy_selection(view, skip_columns=skip_columns))
    act.setShortcut(QKeySequence.StandardKey.Copy)
    act.setEnabled(has)
    act = menu.addAction("Copy with headers", lambda: copy_selection(view, headers=True, skip_columns=skip_columns))
    act.setEnabled(has)
    rows, _cols = _visible(view, skip_columns)
    menu.addAction(f"Copy all shown rows ({len(rows)}) with headers", lambda: copy_all(view, skip_columns=skip_columns))
    if extra is not None:
        extra(menu, index)
    return menu


def install_copy(view: QAbstractItemView, *, skip_columns=(), extra=None) -> None:
    """Ctrl+C and a right-click Copy menu on ``view``. ``extra(menu, index)`` may add more actions."""
    view.installEventFilter(_CopyKeys(view, skip_columns))
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    view.customContextMenuRequested.connect(
        lambda pos: copy_menu(view, pos, skip_columns, extra).exec(view.viewport().mapToGlobal(pos)))
