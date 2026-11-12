"""Audit log: who did what and when. Append-only; each entry carries the hash of the one
before it, so a changed or deleted entry is detected by :func:`verify`.

Entries never contain patient data: accessions, strain names, file names and counts only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .db import Database, now

GENESIS = "0" * 64


def _digest(prev: str, at: str, user_id, username: str, action: str, target: str, details: str) -> str:
    text = json.dumps([prev, at, user_id, username, action, target, details], separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record(db: Database, user, action: str, target: str = "", con: sqlite3.Connection | None = None,
           **details) -> None:
    """Add an entry; ``user`` is an auth.User or None (the system). Pass ``con`` to join a transaction."""
    if con is None:
        with db.tx() as c:
            record(db, user, action, target, con=c, **details)
        return
    prev = con.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = prev["hash"] if prev else GENESIS
    at = now()
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", "") or ("system" if user is None else "")
    body = json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)
    con.execute("INSERT INTO audit(at, user_id, username, action, target, details, prev_hash, hash) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (at, user_id, username, action, str(target), body, prev_hash,
                 _digest(prev_hash, at, user_id, username, action, str(target), body)))


def entries(db: Database, action: str = "", user: str = "", text: str = "", limit: int = 500) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM audit WHERE 1 = 1", []
    if action:
        sql += " AND action LIKE ?"
        args.append(action.rstrip("*") + "%")
    if user:
        sql += " AND username = ?"
        args.append(user)
    if text:
        sql += " AND (target LIKE ? OR details LIKE ?)"
        args += [f"%{text}%", f"%{text}%"]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.read() as con:
        return con.execute(sql, args).fetchall()


def verify(db: Database) -> tuple[bool, int, str]:
    """(intact, entries checked, problem). Walks the whole chain from the first entry."""
    prev = GENESIS
    n = 0
    with db.read() as con:
        for row in con.execute("SELECT * FROM audit ORDER BY id"):
            n += 1
            if row["prev_hash"] != prev:
                return False, n, f"entry {row['id']} does not follow the entry before it (an entry was removed)"
            expected = _digest(prev, row["at"], row["user_id"], row["username"], row["action"], row["target"],
                               row["details"])
            if expected != row["hash"]:
                return False, n, f"entry {row['id']} was changed after it was written"
            prev = row["hash"]
    return True, n, ""
