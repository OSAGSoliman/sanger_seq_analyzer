"""The server database: one SQLite file on the server's own disk (never on a network share).

WAL mode lets pages read while a run or a review is being written. Each change of the
schema is one entry of ``MIGRATIONS``; ``PRAGMA user_version`` records how many ran.
Before migrating an existing database a copy is put in ``backups/``.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1: users, sign-in sessions, audit log, settings, QC runs and reviews, Fetch data files
    """
    CREATE TABLE users(
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('viewer', 'analyst', 'admin')),
        pw_hash TEXT NOT NULL,
        must_change INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
        failed INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        created_at TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        prefs TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE sessions(
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE audit(
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        user_id INTEGER,
        username TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '{}',
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX audit_action ON audit(action, at);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE runs(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
        started_at TEXT,
        finished_at TEXT,
        n_samples INTEGER NOT NULL DEFAULT 0,
        n_pass INTEGER NOT NULL DEFAULT 0,
        n_fail INTEGER NOT NULL DEFAULT 0,
        n_files INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        app_version TEXT NOT NULL,
        config TEXT NOT NULL
    );
    CREATE TABLE run_samples(
        run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL,
        sample TEXT NOT NULL,
        status TEXT NOT NULL,
        reasons TEXT NOT NULL DEFAULT '[]',
        reads TEXT NOT NULL DEFAULT '{}',
        length INTEGER NOT NULL DEFAULT 0,
        consensus_sha TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (run_id, idx)
    );
    CREATE TABLE reviews(
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL,
        decision TEXT CHECK (decision IN ('PASS', 'FAIL') OR decision IS NULL),
        previous TEXT,
        user_id INTEGER REFERENCES users(id),
        at TEXT NOT NULL
    );
    CREATE INDEX reviews_run ON reviews(run_id, idx, id);
    CREATE TABLE source_uploads(
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL,
        name TEXT NOT NULL,
        stored TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        uploaded_by INTEGER REFERENCES users(id),
        uploaded_at TEXT NOT NULL
    );
    CREATE TABLE template_versions(
        id INTEGER PRIMARY KEY,
        version INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        stored TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL,
        reason TEXT NOT NULL
    );
    """,
]


class DatabaseError(RuntimeError):
    pass


def now() -> str:
    """Current time in UTC as stored in the database (ISO 8601 with 'Z')."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


class Database:
    def __init__(self, path: str | Path, backups: Path | None = None):
        self.path = Path(path)
        self.backups = backups

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # autocommit; tx() opens transactions
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 10000")
        return con

    @contextlib.contextmanager
    def read(self):
        con = self.connect()
        try:
            yield con
        finally:
            con.close()

    @contextlib.contextmanager
    def tx(self):
        """A write transaction (BEGIN IMMEDIATE: one writer at a time, readers carry on)."""
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    # --- schema ------------------------------------------------------------------------------------------

    def version(self) -> int:
        with self.read() as con:
            return con.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> list[int]:
        """Bring the schema up to date; returns the migrations that ran."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as con:
            con.execute("PRAGMA journal_mode = WAL")
        current = self.version()
        target = len(MIGRATIONS)
        if current > target:
            raise DatabaseError(f"the database was made by a newer version of Sanger Seq Analyzer (schema "
                                f"{current}, this version knows {target}). Install the newer version again.")
        if current == target:
            return []
        if current > 0 and self.backups is not None:
            self.backup(self.backups / f"before-upgrade-schema{current}-{now().replace(':', '')}.db")
        ran = []
        for number in range(current + 1, target + 1):
            con = self.connect()
            try:
                con.executescript("BEGIN IMMEDIATE;" + MIGRATIONS[number - 1] +
                                  f";PRAGMA user_version = {number};COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise
            finally:
                con.close()
            ran.append(number)
        return ran

    def backup(self, dest: str | Path) -> Path:
        """A consistent copy of the live database (safe while the server runs)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src, out = self.connect(), sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        return dest

    # --- settings (JSON values) --------------------------------------------------------------------------

    def get_setting(self, key: str, default=None):
        with self.read() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value, con: sqlite3.Connection | None = None) -> None:
        sql = "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        if con is not None:
            con.execute(sql, (key, json.dumps(value)))
            return
        with self.tx() as c:
            c.execute(sql, (key, json.dumps(value)))
