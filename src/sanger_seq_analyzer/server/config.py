"""Server settings from ``<data>/config.toml`` (written with defaults on first start).

Things that change while the server runs (folder roots, lab defaults, users) are kept
in the database and edited on the Admin page; this file holds only what is needed to start.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_TOML = """\
# Sanger Seq Analyzer server settings. Restart the service after editing.

# Address and port to listen on. 0.0.0.0 = every network card of this server.
host = "0.0.0.0"
port = 8443

# HTTPS: PEM files from your hospital's certificate authority (strongly recommended:
# browsers only allow copy-to-clipboard and secure cookies over HTTPS).
# Leave both empty to serve plain HTTP (for a first test on one PC only).
tls_cert = ""
tls_key = ""

# Sign-in: minutes of inactivity before signing out, and the longest a sign-in lasts.
session_idle_minutes = 30
session_max_hours = 12

# Local time zone of the lab (used for TAT, ISO weeks in strain names, and times shown).
timezone = "Asia/Dubai"

# Upload limits.
max_ab1_mb = 10
max_files_per_run = 500
max_sheet_mb = 100

# The address users type (e.g. "https://seqqc.lab.local:8443"). Requests that change
# data must come from this site. Leave empty to accept the address of each request.
public_url = ""
"""


@dataclass
class Config:
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8443
    tls_cert: str = ""
    tls_key: str = ""
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    timezone: str = "Asia/Dubai"
    max_ab1_mb: int = 10
    max_files_per_run: int = 500
    max_sheet_mb: int = 100
    public_url: str = ""
    extra: dict = field(default_factory=dict)  # unknown keys, kept for messages

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sanger_seq.db"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def https(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    def make_dirs(self) -> None:
        for d in (self.data_dir, self.runs_dir, self.sources_dir, self.templates_dir, self.backups_dir,
                  self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


def load(data_dir: str | Path, create: bool = True) -> Config:
    """Read ``config.toml`` in ``data_dir`` (creating the folder and a default file if asked)."""
    data_dir = Path(data_dir).expanduser().resolve()
    path = data_dir / "config.toml"
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(DEFAULT_TOML, encoding="utf-8")
    # utf-8-sig: Windows PowerShell 5 writes a byte-order mark when it edits the file
    values = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    known = {f.name for f in fields(Config)} - {"data_dir", "extra"}
    cfg = Config(data_dir=data_dir, **{k: v for k, v in values.items() if k in known})
    cfg.extra = {k: v for k, v in values.items() if k not in known}
    port = os.environ.get("SANGER_SEQ_PORT")
    if port:
        cfg.port = int(port)
    for name in ("tls_cert", "tls_key"):
        value = getattr(cfg, name)
        if value and not Path(value).is_absolute():
            setattr(cfg, name, str(data_dir / value))
    return cfg
