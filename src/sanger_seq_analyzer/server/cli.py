"""``sanger-seq serve`` and ``sanger-seq server …`` (create-admin, add-user, passwd, backup, check)."""

from __future__ import annotations

import getpass
import logging
import logging.handlers
import os
import socket
import sys
import zipfile
from datetime import datetime
from pathlib import Path

_INSTANCE = None
DATA_HELP = "the server's data folder (database, runs, uploads, config.toml); env SANGER_SEQ_DATA"


def add_parsers(sub) -> None:
    p = sub.add_parser("serve", help="run the web server for the lab (see DEPLOY.md)")
    p.add_argument("--data", default=os.environ.get("SANGER_SEQ_DATA"), help=DATA_HELP)
    p.add_argument("--host", help="address to listen on (default: config.toml)")
    p.add_argument("--port", type=int, help="port (default: config.toml)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("server", help="manage the web server: users, backups, checks")
    p.add_argument("--data", default=os.environ.get("SANGER_SEQ_DATA"), help=DATA_HELP)
    s = p.add_subparsers(dest="server_command", required=True)
    c = s.add_parser("create-admin", help="create an admin account (asks for its password)")
    c.add_argument("--username", default="admin")
    c.add_argument("--name", default="")
    c.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    c.set_defaults(func=cmd_create_admin)
    c = s.add_parser("add-user", help="add a user; prints a temporary password")
    c.add_argument("--username", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--role", choices=("viewer", "analyst", "admin"), default="analyst")
    c.set_defaults(func=cmd_add_user)
    c = s.add_parser("passwd", help="give a user a new temporary password (also unlocks the account)")
    c.add_argument("--username", required=True)
    c.set_defaults(func=cmd_passwd)
    c = s.add_parser("backup", help="write a consistent backup zip (safe while the server runs)")
    c.add_argument("--out", help="zip file to write (default: <data>/backups/sanger-seq-backup-<time>.zip)")
    c.set_defaults(func=cmd_backup)
    c = s.add_parser("check", help="show the settings, database and folder access")
    c.set_defaults(func=cmd_check)
    c = s.add_parser("health", help="exit 0 when the running server answers (for Docker and monitoring)")
    c.set_defaults(func=cmd_health)


def _need_server():
    import importlib.util
    missing = [m for m in ("fastapi", "uvicorn", "jinja2", "multipart") if importlib.util.find_spec(m) is None]
    if missing:
        print(f"The web server is not included in this installation ({', '.join(missing)} missing).\n"
              'Install it with: pip install "sanger_seq_analyzer[server]"', file=sys.stderr)
        raise SystemExit(1)


def _config(args):
    from .config import load
    if not args.data:
        print("Give the data folder with --data (or set SANGER_SEQ_DATA).", file=sys.stderr)
        raise SystemExit(2)
    return load(args.data)


def _accounts(cfg):
    from .auth import Accounts
    from .db import Database
    cfg.make_dirs()
    db = Database(cfg.db_path, backups=cfg.backups_dir)
    db.migrate()
    return db, Accounts(db, cfg.session_idle_minutes, cfg.session_max_hours)


def setup_logging(cfg) -> Path:
    folder = cfg.data_dir / "logs"
    folder.mkdir(exist_ok=True)
    path = folder / "server.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(console)
    return path


class SingleInstance:
    """Only one server per data folder: the jobs, locks and caches live in one process."""

    def __init__(self, folder: Path):
        self.path = folder / "server.lock"
        self.fh = open(self.path, "a+")  # noqa: SIM115 (kept open while the server runs)
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise SystemExit(f"Another Sanger Seq Analyzer server is already using {folder}. Stop it first.") \
                from None
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"{os.getpid()}\n")
        self.fh.flush()


def cmd_serve(args) -> int:
    _need_server()
    import uvicorn

    from .. import __version__
    from .app import create_app
    cfg = _config(args)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    cfg.make_dirs()
    global _INSTANCE
    _INSTANCE = SingleInstance(cfg.data_dir)  # held until the process ends
    log_path = setup_logging(cfg)
    for name in ("tls_cert", "tls_key"):
        value = getattr(cfg, name)
        if value and not Path(value).is_file():
            print(f"{name} in config.toml points to a missing file: {value}", file=sys.stderr)
            return 2
    app = create_app(cfg)
    scheme = "https" if cfg.https else "http"
    host = socket.getfqdn() if cfg.host in ("0.0.0.0", "::") else cfg.host
    log = logging.getLogger("sanger_seq_analyzer.server")
    log.info("Sanger Seq Analyzer server %s at %s://%s:%s/ (data: %s, log: %s)", __version__, scheme, host, cfg.port,
             cfg.data_dir, log_path)
    if app.state.ctx.accounts.count_admins() == 0:
        log.warning("No admin account yet. Create one with: sanger-seq server create-admin --data \"%s\"",
                    cfg.data_dir)
    uvicorn.run(app, host=cfg.host, port=cfg.port, ssl_certfile=cfg.tls_cert or None, ssl_keyfile=cfg.tls_key or None,
                log_config=None, proxy_headers=False, server_header=False, timeout_keep_alive=30)
    return 0


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password for the new admin (at least 12 characters): ")
    if getpass.getpass("Same password again: ") != first:
        raise SystemExit("The two passwords differ.")
    return first


def cmd_create_admin(args) -> int:
    from . import audit
    from .auth import AuthError
    cfg = _config(args)
    db, accounts = _accounts(cfg)
    try:
        user = accounts.create(args.username, args.name or args.username, "admin", _read_password(args),
                               must_change=False)
    except AuthError as exc:
        print(exc, file=sys.stderr)
        return 1
    audit.record(db, None, "user.created", user.username, role="admin", via="command line")
    print(f"Admin '{user.username}' created. Sign in at the server's address.")
    return 0


def cmd_add_user(args) -> int:
    from . import audit
    from .auth import AuthError
    from .tools.admin import temporary_password
    cfg = _config(args)
    db, accounts = _accounts(cfg)
    password = temporary_password(args.username)
    try:
        user = accounts.create(args.username, args.name or args.username, args.role, password, must_change=True)
    except AuthError as exc:
        print(exc, file=sys.stderr)
        return 1
    audit.record(db, None, "user.created", user.username, role=args.role, via="command line")
    print(f"User '{user.username}' ({args.role}) created. Temporary password: {password}\n"
          "They choose their own password at first sign-in.")
    return 0


def cmd_passwd(args) -> int:
    from . import audit
    from .tools.admin import temporary_password
    cfg = _config(args)
    db, accounts = _accounts(cfg)
    user = accounts.by_name(args.username)
    if user is None:
        print(f"No user called {args.username}.", file=sys.stderr)
        return 1
    password = temporary_password(user.username)
    accounts.set_password(user.id, password, must_change=True)
    audit.record(db, None, "user.password_reset", user.username, via="command line")
    print(f"New temporary password for '{user.username}': {password}")
    return 0


def backup(cfg, out: Path | None = None) -> Path:
    """Zip the database (consistent copy), config, runs, uploaded sources and template versions."""
    from .db import Database
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = Path(out) if out else cfg.backups_dir / f"sanger-seq-backup-{stamp}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    snapshot = cfg.tmp_dir / f"backup-{stamp}.db"
    Database(cfg.db_path).backup(snapshot)
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, "sanger_seq.db")
            if (cfg.data_dir / "config.toml").exists():
                z.write(cfg.data_dir / "config.toml", "config.toml")
            for folder in (cfg.runs_dir, cfg.sources_dir, cfg.templates_dir):
                for f in sorted(folder.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(cfg.data_dir).as_posix())
    finally:
        snapshot.unlink(missing_ok=True)
    return out


def cmd_backup(args) -> int:
    cfg = _config(args)
    _accounts(cfg)
    print(f"Backup written: {backup(cfg, args.out)}")
    return 0


def cmd_check(args) -> int:
    from . import audit, paths
    cfg = _config(args)
    db, accounts = _accounts(cfg)
    print(f"Data folder: {cfg.data_dir}\nDatabase schema: {db.version()}\nAddress: "
          f"{'https' if cfg.https else 'http'}://{cfg.host}:{cfg.port}/\nTime zone: {cfg.timezone}")
    print(f"Users: {len(accounts.users())} ({accounts.count_admins()} admin)")
    ok, n, problem = audit.verify(db)
    print(f"Audit log: {n} entries, {'intact' if ok else 'CHANGED: ' + problem}")
    for r in paths.roots(db):
        for name, good, detail in paths.check_access(r):
            print(f"Folder '{r.name}' ({r.path}): {name}: {'OK' if good else 'NO'} {detail}")
    return 0 if ok else 1


def cmd_health(args) -> int:
    import json
    import ssl
    import urllib.request
    cfg = _config(args)
    scheme = "https" if cfg.https else "http"
    context = ssl._create_unverified_context() if cfg.https else None  # loopback only: the name will not match
    try:
        with urllib.request.urlopen(f"{scheme}://127.0.0.1:{cfg.port}/healthz", timeout=5, context=context) as r:
            ok = json.load(r).get("status") == "ok"
    except OSError as exc:
        print(f"not answering: {exc}", file=sys.stderr)
        return 1
    print("ok" if ok else "unhealthy")
    return 0 if ok else 1
