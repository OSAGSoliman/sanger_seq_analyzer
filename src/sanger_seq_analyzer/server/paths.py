"""Folders on the server that users may browse (sequencer exports) or that hold the lab files.

Users never type a server path. An admin defines each folder ("root") once; pages then
send the root's id and a path relative to it, which is checked to stay inside the root.
On Windows use UNC paths (\\\\fileserver\\share\\...): a service cannot see mapped drives.
"""

from __future__ import annotations

import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .db import Database

ROOTS_KEY = "roots"
_BAD_PART = re.compile(r"^(\.\.?|.*[:\\].*)$")


class PathError(ValueError):
    pass


@dataclass(frozen=True)
class Root:
    id: str
    name: str
    path: str
    writable: bool = False  # the server may write here (e.g. the MeaNS template's folder)

    @property
    def folder(self) -> Path:
        return Path(self.path)


def roots(db: Database) -> list[Root]:
    return [Root(**r) for r in db.get_setting(ROOTS_KEY, [])]


def root(db: Database, root_id: str) -> Root:
    for r in roots(db):
        if r.id == root_id:
            return r
    raise PathError("This folder is not set up on the server (ask an admin).")


def save_roots(db: Database, items: list[Root]) -> None:
    db.set_setting(ROOTS_KEY, [r.__dict__ for r in items])


def new_root_id() -> str:
    return secrets.token_hex(4)


def resolve(base: Root, relative: str = "") -> Path:
    """``relative`` (forward slashes) inside ``base``; raises PathError for anything that leaves it."""
    relative = (relative or "").strip().strip("/")
    parts = [p for p in PurePosixPath(relative).parts if p not in ("", "/")] if relative else []
    for part in parts:
        if _BAD_PART.match(part) or part.startswith("~"):
            raise PathError(f"'{part}' is not allowed in a folder name here.")
    top = base.folder.resolve()
    target = top.joinpath(*parts).resolve() if parts else top
    if os.path.normcase(str(target)) != os.path.normcase(str(top)) and not _inside(target, top):
        raise PathError("That folder is outside the shared folder.")
    return target


def _inside(target: Path, top: Path) -> bool:
    t, b = os.path.normcase(str(target)), os.path.normcase(str(top))
    return t.startswith(b.rstrip("\\/") + os.sep)


def relative_to(base: Root, path: Path) -> str:
    return path.resolve().relative_to(base.folder.resolve()).as_posix()


def list_folder(base: Root, relative: str = "", suffixes: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """(sub-folders, files with one of ``suffixes``) in a folder of ``base``, sorted by name."""
    folder = resolve(base, relative)
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise PathError(f"The folder could not be read: {exc.strerror or exc}") from exc
    dirs, files = [], []
    for e in entries:
        if e.name.startswith((".", "~$")):
            continue
        try:
            if e.is_dir():
                dirs.append(e.name)
            elif e.is_file() and (not suffixes or e.suffix.lower() in suffixes):
                files.append(e.name)
        except OSError:
            continue
    return dirs, files


def check_access(base: Root) -> list[tuple[str, bool, str]]:
    """What the server's account can do in a root: [(check, ok, detail)]."""
    out = []
    folder = base.folder
    try:
        n = sum(1 for _ in folder.iterdir())
        out.append(("List files", True, f"{n} entries"))
    except OSError as exc:
        out.append(("List files", False, str(exc)))
        return out
    if not base.writable:
        return out
    try:
        fd, name = tempfile.mkstemp(prefix=".ssa-check-", dir=folder)
        os.write(fd, b"check")
        os.close(fd)
        moved = Path(name).with_name(Path(name).name + ".renamed")
        os.replace(name, moved)
        moved.unlink()
        out.append(("Create, rename and delete a file", True, "ok"))
    except OSError as exc:
        out.append(("Create, rename and delete a file", False, str(exc)))
    return out
