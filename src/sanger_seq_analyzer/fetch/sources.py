"""The lab's source spreadsheets: what each one is called, finding the newest, reading them.

Shared by the desktop app (paths chosen with Browse…) and the server (a configured
shared folder and uploaded versions). Nothing here depends on Qt.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

from . import tables
from .linking import DAILY, LINELIST, MASTERLIST, TEMPLATE, SourceSpec

log = logging.getLogger("sanger_seq_analyzer.fetch")

SPECS: dict[str, SourceSpec] = {s.key: s for s in (MASTERLIST, DAILY, LINELIST, TEMPLATE)}
# key -> (label, the lab's file name, name patterns used by Browse… and to spot a newer version in the same folder)
SOURCE_FILES = {
    "masterlist": ("Measles and rubella master list", "Measles and Rubella Positive Masterlist 2026",
                   ("*masterlist*",)),
    "daily": ("Measles daily sheet", "Weekly update on measles-rubella results 09112026",
              ("*measles*rubella*results*", "*daily*sheet*")),
    "linelist": ("MOHAP line list", "UAE_EMRO Measles Rubella Reporting Tool Sep 2026",
                 ("*reporting tool*", "*line*list*")),
    "template": ("MeaNS upload template", "MeaNS Upload Template 2025-2026", ("*means*template*",)),
}

Stamp = tuple  # (resolved path, mtime_ns, size)


def read_source(spec: SourceSpec, path: str | Path) -> tables.Table:
    return tables.read_table(path, spec.title, list(spec.expected), spec.match_column, combine=spec.combine)


def is_candidate(key: str, name: str) -> bool:
    """True for a readable spreadsheet whose name matches the patterns of source ``key``."""
    name = name.lower()
    if name.startswith(("~$", ".")) or Path(name).suffix not in tables.READABLE_SUFFIXES:
        return False
    return any(fnmatch.fnmatch(name, p) for p in SOURCE_FILES[key][2])


def newest_in_folder(key: str, folder: str | Path, newer_than: float = -1.0,
                     exclude: Path | None = None) -> Path | None:
    """The most recently modified file of kind ``key`` in ``folder`` (not recursive), or None."""
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return None
    skip = None
    if exclude is not None:
        try:
            skip = exclude.resolve()
        except OSError:
            skip = None
    best, best_time = None, newer_than
    for f in entries:
        if not is_candidate(key, f.name):
            continue
        try:
            if not f.is_file() or (skip is not None and f.resolve() == skip):
                continue
            t = f.stat().st_mtime
        except OSError:
            continue
        if t > best_time:
            best, best_time = f, t
    return best


def newer_file(key: str, path: str | Path | None) -> Path | None:
    """A more recently saved file of the same kind in ``path``'s folder (e.g. next week's sheet)."""
    if not path:
        return None
    current = Path(path)
    try:
        current_time = current.stat().st_mtime if current.is_file() else -1.0
    except OSError:
        return None
    return newest_in_folder(key, current.parent, current_time, exclude=current)


def stamp(path: str | Path) -> Stamp | None:
    """(resolved path, mtime, size): changes when the file is saved again."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)


def read_one(key: str, path: str | Path) -> tuple[Stamp | None, tables.Table | None, str]:
    """Read source ``key`` from ``path``: (stamp, table or None, error message or "")."""
    st = stamp(path)
    if st is None:
        return None, None, f"file not found: {path}"
    try:
        return st, read_source(SPECS[key], path), ""
    except tables.TableError as exc:
        return st, None, str(exc)
    except Exception as exc:  # unexpected file content: report it, never crash the caller
        log.exception("reading %s failed", Path(path).name)
        return st, None, f"{type(exc).__name__}: {exc}"


def strain_names(template: tables.Table | None) -> list:
    """Every WHO strain name already in the template table (numbering continues after them)."""
    if template is None:
        return []
    col = template.column("WHO Strain Name")
    return [r.get(col) for r in template.rows] if col else []
