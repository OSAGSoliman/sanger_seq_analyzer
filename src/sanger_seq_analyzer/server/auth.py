"""Accounts, passwords, sign-in sessions and roles.

* Passwords: scrypt (standard library) with a random salt per user.
* Sessions: a random token in an HttpOnly cookie; the database keeps only its SHA-256,
  so a copy of the database cannot be used to sign in. A session ends after the idle
  time, after the maximum time, or when the user's password or role changes.
* Roles: viewer (look and download) < analyst (run, review, fetch, save) < admin.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db import Database, now, parse_time

ROLES = ("viewer", "analyst", "admin")
ROLE_NAMES = {"viewer": "Viewer", "analyst": "Analyst", "admin": "Admin"}
ROLE_HELP = {
    "viewer": "can look at runs and download results",
    "analyst": "can also start runs, review samples, use Fetch data and save to the MeaNS template",
    "admin": "can also manage users, folders and lab defaults, and read the audit log",
}
MIN_PASSWORD = 12
MAX_FAILURES = 10
LOCK_MINUTES = 15
COOKIE = "ssa_session"
_SCRYPT = (2 ** 14, 8, 1)  # n, r, p (about 16 MB of memory per check)


class AuthError(Exception):
    """A sign-in or account problem, worded for the user."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = _SCRYPT
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${n}${r}${p}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def password_problem(password: str, username: str = "") -> str:
    """Why a new password is not accepted, or ""."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters (a short sentence is easy to remember)."
    if username and username.casefold() in password.casefold():
        return "The password must not contain the user name."
    if len(set(password)) < 5:
        return "The password is too simple."
    return ""


_DUMMY_HASH = hash_password("not a real password")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    must_change: bool
    active: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> User:
        return cls(row["id"], row["username"], row["display_name"], row["role"], bool(row["must_change"]),
                   bool(row["active"]))

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


@dataclass(frozen=True)
class Session:
    user: User
    csrf: str
    token_hash: str


class Accounts:
    def __init__(self, db: Database, idle_minutes: int = 30, max_hours: int = 12):
        self.db = db
        self.idle = timedelta(minutes=idle_minutes)
        self.max_age = timedelta(hours=max_hours)

    # --- users -------------------------------------------------------------------------------------------

    def count_admins(self, con=None) -> int:
        sql = "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
        if con is not None:
            return con.execute(sql).fetchone()[0]
        with self.db.read() as c:
            return c.execute(sql).fetchone()[0]

    def users(self) -> list[sqlite3.Row]:
        with self.db.read() as con:
            return con.execute("SELECT * FROM users ORDER BY active DESC, username COLLATE NOCASE").fetchall()

    def get(self, user_id: int) -> User | None:
        with self.db.read() as con:
            row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User.from_row(row) if row else None

    def by_name(self, username: str) -> User | None:
        with self.db.read() as con:
            row = con.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        return User.from_row(row) if row else None

    def create(self, username: str, display_name: str, role: str, password: str, must_change: bool = True) -> User:
        username = username.strip()
        if not username or not all(ch.isalnum() or ch in "._-" for ch in username) or len(username) > 40:
            raise AuthError("User names use letters, digits, '.', '_' or '-' (at most 40).")
        if role not in ROLES:
            raise AuthError(f"Unknown role {role!r}.")
        problem = password_problem(password, username)
        if problem:
            raise AuthError(problem)
        stamp = now()
        try:
            with self.db.tx() as con:
                cur = con.execute(
                    "INSERT INTO users(username, display_name, role, pw_hash, must_change, created_at, changed_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (username, display_name.strip() or username, role, hash_password(password), int(must_change),
                     stamp, stamp))
        except sqlite3.IntegrityError:
            raise AuthError(f"There is already a user called {username}.") from None
        return self.get(cur.lastrowid)

    def update(self, user_id: int, *, display_name: str | None = None, role: str | None = None,
               active: bool | None = None) -> User:
        with self.db.tx() as con:
            row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthError("No such user.")
            new_role = role if role is not None else row["role"]
            new_active = int(active) if active is not None else row["active"]
            if new_role not in ROLES:
                raise AuthError(f"Unknown role {role!r}.")
            if row["role"] == "admin" and row["active"] and (new_role != "admin" or not new_active) \
                    and self.count_admins(con) <= 1:
                raise AuthError("This is the last admin. Make another user admin first.")
            con.execute("UPDATE users SET display_name = ?, role = ?, active = ?, changed_at = ? WHERE id = ?",
                        ((display_name or row["display_name"]).strip(), new_role, new_active, now(), user_id))
            if new_role != row["role"] or not new_active:
                con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return self.get(user_id)

    def set_password(self, user_id: int, password: str, must_change: bool) -> None:
        user = self.get(user_id)
        if user is None:
            raise AuthError("No such user.")
        problem = password_problem(password, user.username)
        if problem:
            raise AuthError(problem)
        with self.db.tx() as con:
            con.execute("UPDATE users SET pw_hash = ?, must_change = ?, failed = 0, locked_until = NULL, "
                        "changed_at = ? WHERE id = ?", (hash_password(password), int(must_change), now(), user_id))
            con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def change_own_password(self, user: User, current: str, new: str) -> None:
        with self.db.read() as con:
            row = con.execute("SELECT pw_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        if row is None or not verify_password(current, row["pw_hash"]):
            raise AuthError("The current password is not right.")
        if current == new:
            raise AuthError("Choose a password different from the current one.")
        self.set_password(user.id, new, must_change=False)

    def set_prefs(self, user_id: int, **values) -> None:
        import json
        with self.db.tx() as con:
            row = con.execute("SELECT prefs FROM users WHERE id = ?", (user_id,)).fetchone()
            prefs = json.loads(row["prefs"]) if row else {}
            prefs.update(values)
            con.execute("UPDATE users SET prefs = ? WHERE id = ?", (json.dumps(prefs), user_id))

    def prefs(self, user_id: int) -> dict:
        import json
        with self.db.read() as con:
            row = con.execute("SELECT prefs FROM users WHERE id = ?", (user_id,)).fetchone()
        return json.loads(row["prefs"]) if row else {}

    # --- sign in -----------------------------------------------------------------------------------------

    def sign_in(self, username: str, password: str) -> tuple[str, User]:
        """Check the password; returns (cookie token, user). Raises AuthError with a message for the page."""
        generic = "The user name or password is not right."
        error = ""
        with self.db.tx() as con:  # counting failures must be committed, so errors are raised after the commit
            row = con.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
            locked = parse_time(row["locked_until"]) if row is not None else None
            if row is None:
                verify_password(password, _DUMMY_HASH)  # take as long as a real check
                error = generic
            elif locked and locked > datetime.now(UTC):
                minutes = max(1, int((locked - datetime.now(UTC)).total_seconds() // 60) + 1)
                error = (f"Too many wrong passwords. Try again in {minutes} minutes, or ask an admin to reset "
                         "the password.")
            elif not verify_password(password, row["pw_hash"]):
                failed, until = row["failed"] + 1, None
                if failed >= MAX_FAILURES:
                    until = (datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    failed = 0
                con.execute("UPDATE users SET failed = ?, locked_until = ? WHERE id = ?", (failed, until, row["id"]))
                error = generic
            elif not row["active"]:
                error = "This account is switched off. Ask an admin."
            else:
                con.execute("UPDATE users SET failed = 0, locked_until = NULL WHERE id = ?", (row["id"],))
                token = secrets.token_urlsafe(32)
                stamp = now()
                con.execute("INSERT INTO sessions(token_hash, user_id, csrf, created_at, last_seen) "
                            "VALUES(?, ?, ?, ?, ?)", (_token_hash(token), row["id"], secrets.token_urlsafe(32),
                                                      stamp, stamp))
                con.execute("DELETE FROM sessions WHERE created_at < ?",
                            ((datetime.now(UTC) - self.max_age).strftime("%Y-%m-%dT%H:%M:%SZ"),))
        if error:
            raise AuthError(error)
        return token, User.from_row(row)

    def session(self, token: str | None) -> Session | None:
        """The live session for a cookie token, or None (expired sessions are removed)."""
        if not token or len(token) > 100:
            return None
        th = _token_hash(token)
        with self.db.read() as con:
            row = con.execute("SELECT s.*, u.id AS uid, u.username, u.display_name, u.role, u.must_change, u.active "
                              "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                              (th,)).fetchone()
        if row is None:
            return None
        current = datetime.now(UTC)
        created, seen = parse_time(row["created_at"]), parse_time(row["last_seen"])
        if not row["active"] or current - seen > self.idle or current - created > self.max_age:
            self.sign_out(token)
            return None
        if (current - seen).total_seconds() > 30:
            with self.db.tx() as con:
                con.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now(), th))
        user = User(row["uid"], row["username"], row["display_name"], row["role"], bool(row["must_change"]),
                    bool(row["active"]))
        return Session(user, row["csrf"], th)

    def sign_out(self, token: str | None) -> None:
        if token:
            with self.db.tx() as con:
                con.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
