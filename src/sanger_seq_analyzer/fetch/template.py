"""Append confirmed rows to the MeaNS upload template workbook.

The workbook is copied to a ``MeaNS backups`` folder next to it before anything
is written, and saved through a temporary file so an error never leaves a
half-written workbook. Each new cell copies the style and number format of the
cell above it, so dates look like the rows already there.

Only one save at a time: a ``.<name>.saving.lock`` file next to the template is
created exclusively for the duration of the save, so the desktop app and the
server (or two PCs) never append to the same template at once.
"""

from __future__ import annotations

import contextlib
import copy
import os
import shutil
import socket
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .tables import TableError, as_text, clean_header, find_header, match_column, norm_key

BACKUP_FOLDER = "MeaNS backups"
STALE_LOCK_SECONDS = 120  # a saving lock older than this was left by a crash


@dataclass
class AppendResult:
    path: Path
    sheet: str
    first_row: int
    last_row: int
    written: int
    backup: Path | None
    columns: list[str]  # template columns that received values
    unmatched: list[str] = field(default_factory=list)  # our columns with no template column
    skipped: list[str] = field(default_factory=list)  # rows not written, with the reason


def lock_file(path: Path) -> Path | None:
    """Excel's owner file (``~$name.xlsx``) when the workbook is open in Excel."""
    lock = path.with_name("~$" + path.name[2:]) if len(path.name) > 2 else None
    for candidate in (path.with_name("~$" + path.name), lock):
        if candidate is not None and candidate.exists():
            return candidate
    return None


def saving_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.saving.lock")


@contextlib.contextmanager
def saving_lock(path: str | Path):
    """Hold the template's saving lock; raises TableError when someone else is saving it."""
    lock = saving_lock_path(Path(path))
    for attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                who = lock.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                age, who = 0.0, ""
            if attempt == 0 and age > STALE_LOCK_SECONDS:
                _remove(lock)
                continue
            raise TableError(f"{Path(path).name} is being saved by someone else"
                             f"{f' ({who})' if who else ''}. Try again in a minute.") from None
        except OSError as exc:
            raise TableError(f"{Path(path).name} cannot be saved: the folder is not writable ({exc})") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{socket.gethostname()} {datetime.now():%Y-%m-%d %H:%M:%S}")
        yield lock
    finally:
        _remove(lock)


def _backup_name(folder: Path, path: Path) -> Path:
    base = f"{path.stem} {datetime.now():%Y-%m-%d %H%M%S}"
    candidate, n = folder / f"{base}{path.suffix}", 2
    while candidate.exists():
        candidate, n = folder / f"{base} ({n}){path.suffix}", n + 1
    return candidate


def _find_sheet(wb, key: str, expected: list[str]):
    best = None
    for ws in wb.worksheets:
        rows = [tuple(c.value for c in row) for row in ws.iter_rows(min_row=1, max_row=40)]
        if not rows:
            continue
        index, score = find_header(rows, expected)
        cells = [clean_header(v) for v in rows[index] if v not in (None, "")]
        if match_column(key, cells) and (best is None or score > best[2]):
            best = (ws, index + 1, score)
    if best is None:
        raise TableError(f"no sheet of the template has a '{key}' column")
    return best[0], best[1]


def append_rows(path: str | Path, rows: list[dict], expected: list[str], key: str = "WHO Strain Name",
                unique: tuple[str, ...] = ("WHO Strain Name", "Accession"), backup: bool = True) -> AppendResult:
    """Append ``rows`` (column -> value) below the last filled row of the template.

    Rows whose ``unique`` values are already in the template are skipped. Raises
    :class:`TableError` with a readable message when the file cannot be written.
    """
    path = Path(path)
    if not path.is_file():
        raise TableError(f"template not found: {path}")
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise TableError(f"{path.name} cannot be updated: new rows can only be added to an .xlsx or .xlsm "
                         "workbook. Open it in Excel and save it as .xlsx (Excel Workbook), then choose that file")
    with saving_lock(path):
        return _append_rows(path, rows, expected, key, unique, backup)


def _append_rows(path: Path, rows: list[dict], expected: list[str], key: str, unique: tuple[str, ...],
                 backup: bool) -> AppendResult:
    from openpyxl import load_workbook

    lock = lock_file(path)
    if lock is not None:
        raise TableError(f"{path.name} is open in Excel (found {lock.name}). Close it in Excel and try again.")
    keep_vba = path.suffix.lower() == ".xlsm"
    try:
        wb = load_workbook(path, keep_vba=keep_vba)
    except PermissionError as exc:
        raise TableError(f"{path.name} could not be opened: it is open in Excel or another program, or "
                         "read-only. Close it and try again.") from exc
    except Exception as exc:
        raise TableError(f"{path.name} could not be opened ({type(exc).__name__}: {exc})") from exc
    ws, header_row = _find_sheet(wb, key, expected)
    headers = {}
    for cell in ws[header_row]:
        name = clean_header(cell.value)
        if name:
            headers[cell.column] = name
    names = list(headers.values())
    col_of = {}
    unmatched = []
    for ours in (rows[0].keys() if rows else []):
        theirs = match_column(ours, names)
        if theirs is None:
            unmatched.append(ours)
            continue
        col_of[ours] = next(c for c, n in headers.items() if n == theirs)
    # last row with anything in the template's columns (blank lines inside the table are kept)
    last = header_row
    for r in range(ws.max_row, header_row, -1):
        if any(ws.cell(row=r, column=c).value not in (None, "") for c in headers):
            last = r
            break
    existing: dict[str, set[str]] = {}
    for u in unique:
        if u in col_of:
            col = col_of[u]
            existing[u] = {norm_key(ws.cell(row=r, column=col).value) for r in range(header_row + 1, last + 1)}
            existing[u].discard("")
    skipped, to_write = [], []
    for row in rows:
        dup = next((u for u in existing if norm_key(row.get(u)) and norm_key(row.get(u)) in existing[u]), None)
        if dup:
            skipped.append(f"{as_text(row.get('Accession') or row.get(key))}: {dup} {as_text(row.get(dup))} is "
                           f"already in the template")
            continue
        to_write.append(row)
        for u in existing:
            if norm_key(row.get(u)):
                existing[u].add(norm_key(row.get(u)))
    backup_path = None
    if to_write:
        if backup:
            folder = path.parent / BACKUP_FOLDER
            folder.mkdir(exist_ok=True)
            backup_path = _backup_name(folder, path)
            shutil.copy2(path, backup_path)
        # style source per column: the lowest filled cell of that column (dates keep their format)
        style_of = {}
        for col in col_of.values():
            for r in range(last, max(header_row, last - 200), -1):
                if ws.cell(row=r, column=col).value not in (None, ""):
                    style_of[col] = r
                    break
            else:
                if last > header_row:
                    style_of[col] = last
        for i, row in enumerate(to_write):
            r = last + 1 + i
            for ours, col in col_of.items():
                value = row.get(ours)
                if isinstance(value, date) and not isinstance(value, datetime):
                    value = datetime(value.year, value.month, value.day)
                cell = ws.cell(row=r, column=col)
                cell.value = None if value == "" else value
                if col in style_of:
                    above = ws.cell(row=style_of[col], column=col)
                    if above.has_style:
                        cell._style = copy.copy(above._style)
                if isinstance(value, datetime) and not cell.is_date:
                    cell.number_format = "dd/mm/yyyy"
        part = path.with_name(f".{path.stem}.saving{path.suffix}")
        try:
            wb.save(part)
            os.replace(part, path)
        except PermissionError as exc:
            _remove(part)
            raise TableError(f"{path.name} could not be saved: the file is open in another program or read-only. "
                             "Close it and try again (the backup is in the 'MeaNS backups' folder).") from exc
        except OSError as exc:
            _remove(part)
            raise TableError(f"{path.name} could not be saved: {exc}") from exc
    return AppendResult(path, ws.title, last + 1, last + len(to_write), len(to_write), backup_path,
                        [headers[c] for c in col_of.values()], unmatched, skipped)


def _remove(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def write_workbook(path: str | Path, sheets: list[tuple[str, list[str], list[list]]]) -> Path:
    """A new .xlsx with one sheet per (title, columns, rows); dates get a dd/mm/yyyy format."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    path = Path(path)
    wb = Workbook()
    wb.remove(wb.active)
    for title, columns, rows in sheets:
        ws = wb.create_sheet(title[:31])
        ws.append(columns)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append([datetime(v.year, v.month, v.day) if isinstance(v, date) and not isinstance(v, datetime)
                       else v for v in row])
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, datetime):
                    cell.number_format = "dd/mm/yyyy"
        ws.freeze_panes = "A2"
        for i, name in enumerate(columns, 1):
            width = max([len(str(name))] + [len(as_text(r[i - 1])) for r in rows[:200] if i - 1 < len(r)])
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(60, max(8, width + 2))
    part = path.with_name(f".{path.stem}.saving{path.suffix}")
    try:
        wb.save(part)
        os.replace(part, path)
    except OSError as exc:
        _remove(part)
        raise TableError(f"{path.name} could not be saved: {exc}") from exc
    return path
