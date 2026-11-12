"""Sign-in, roles, CSRF, sessions, admin pages, audit log and the database."""

import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ADMIN_PW, csrf_of, new_client, sign_in

from sanger_seq_analyzer.server import audit, auth, db, paths
from sanger_seq_analyzer.server.cli import backup


def test_health_and_security_headers(app):
    c = new_client(app)
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    page = c.get("/login")
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "No admin account exists yet" in page.text  # first run explains create-admin
    assert c.get("/runs").headers["location"].startswith("/login?next=/runs")


def test_passwords_are_hashed_and_checked():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.verify_password("correct horse battery", h) and not auth.verify_password("wrong", h)
    assert auth.password_problem("short") and auth.password_problem("aaaaaaaaaaaaaa")
    assert auth.password_problem("osoliman-is-my-password", "osoliman")


def test_sign_in_lockout_after_ten_wrong_passwords(app, accounts):
    accounts.create("lab1", "Lab One", "analyst", "a long enough password", must_change=False)
    c = new_client(app)
    for _ in range(auth.MAX_FAILURES):
        r = c.post("/login", data={"username": "lab1", "password": "nope", "next": "/"})
        assert r.status_code == 401 and "not right" in r.text
    r = c.post("/login", data={"username": "lab1", "password": "a long enough password", "next": "/"})
    assert r.status_code == 401 and "Too many wrong passwords" in r.text
    # an admin reset unlocks
    user = accounts.by_name("lab1")
    accounts.set_password(user.id, "another long password", must_change=False)
    sign_in(c, "lab1", "another long password")
    failed = [e for e in audit.entries(app.state.ctx.db, "login.failed")]
    assert len(failed) == auth.MAX_FAILURES + 1


def test_roles(admin, make_user):
    viewer, analyst = make_user("viewer1", "viewer"), make_user("analyst1", "analyst")
    assert admin.get("/admin/users").status_code == 200
    for who in (viewer, analyst):
        r = who.get("/admin/users")
        assert r.status_code == 403 and "Admin role" in r.text
    assert viewer.get("/fetch").status_code == 403 and analyst.get("/fetch").status_code == 200
    # the tabs follow the role
    assert ">Admin<" in admin.get("/runs").text and ">Admin<" not in analyst.get("/runs").text


def test_csrf_and_origin_checks(admin):
    r = admin.client.post("/admin/users", data={"username": "x1", "role": "viewer"})  # no token
    assert r.status_code == 403 and "out of date" in r.text
    r = admin.post("/admin/users", data={"username": "x2", "role": "viewer"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = admin.post("/admin/users", data={"username": "x3", "role": "viewer"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and "Temporary password for <b>x3</b>" in r.text


def test_new_user_must_choose_a_password(app, admin):
    r = admin.post("/admin/users", data={"username": "newbie", "display_name": "New Bie", "role": "analyst"})
    temp = r.text.split('id="temp-pw">')[1].split("<")[0]
    c = new_client(app)
    sign_in(c, "newbie", temp)
    assert c.get("/runs").headers["location"] == "/account/password"
    token = csrf_of(c)
    r = c.post("/account/password", data={"current": temp, "new": "my own good password", "again": "my own good password",
                                          "csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/login?changed=1"
    assert c.get("/runs").status_code == 303  # signed out: every session ended with the old password
    sign_in(c, "newbie", "my own good password")
    assert c.get("/runs").status_code == 200


def test_last_admin_cannot_be_removed(app, admin, accounts):
    me = accounts.by_name("admin")
    r = admin.post(f"/admin/users/{me.id}", data={"display_name": "Lab Admin", "role": "viewer", "active": "1"})
    assert "last admin" in r.text
    assert accounts.by_name("admin").role == "admin"


def test_role_change_ends_sessions(app, admin, make_user, accounts):
    analyst = make_user("analyst2", "analyst")
    uid = accounts.by_name("analyst2").id
    admin.post(f"/admin/users/{uid}", data={"display_name": "A", "role": "viewer", "active": "1"})
    assert analyst.get("/runs").status_code == 303  # must sign in again


def test_idle_sessions_expire(app, admin, accounts):
    old = (datetime.now(UTC) - timedelta(minutes=app.state.ctx.config.session_idle_minutes + 1))
    with app.state.ctx.db.tx() as con:
        con.execute("UPDATE sessions SET last_seen = ?", (old.strftime("%Y-%m-%dT%H:%M:%SZ"),))
    assert admin.get("/runs").status_code == 303


def test_audit_chain_detects_changes(app, admin):
    d = app.state.ctx.db
    ok, n, _ = audit.verify(d)
    assert ok and n >= 1
    r = admin.get("/admin/audit")
    assert "none changed" in r.text and "login" in r.text
    assert admin.get("/admin/audit.csv").text.startswith("time (UTC),user,action")
    with d.tx() as con:
        con.execute("UPDATE audit SET target = 'edited' WHERE id = 1")
    ok, _, problem = audit.verify(d)
    assert not ok and "changed" in problem


def test_database_refuses_newer_schema(tmp_path):
    database = db.Database(tmp_path / "x.db")
    assert database.migrate() == list(range(1, len(db.MIGRATIONS) + 1))
    assert database.migrate() == []
    con = sqlite3.connect(tmp_path / "x.db")
    con.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    con.close()
    with pytest.raises(db.DatabaseError, match="newer version"):
        database.migrate()


def test_folder_paths_stay_inside_their_root(tmp_path):
    (tmp_path / "share" / "run1").mkdir(parents=True)
    (tmp_path / "share" / "run1" / "A_216.ab1").write_bytes(b"ABIF")
    (tmp_path / "secret.txt").write_text("x")
    root = paths.Root("r1", "Share", str(tmp_path / "share"))
    assert paths.resolve(root, "run1") == (tmp_path / "share" / "run1").resolve()
    for bad in ("..", "../secret.txt", "run1/../../secret.txt", "C:/Windows", "a\\b", "~root"):
        with pytest.raises(paths.PathError):
            paths.resolve(root, bad)
    dirs, files = paths.list_folder(root, "run1", (".ab1",))
    assert dirs == [] and files == ["A_216.ab1"]
    (tmp_path / "share" / "escape").symlink_to(tmp_path)
    with pytest.raises(paths.PathError):
        paths.resolve(root, "escape")


def test_admin_folders_and_access_check(admin, tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    r = admin.post("/admin/folders", data={"name": "Sequencer", "path": str(share), "writable": "1"})
    assert r.status_code == 200 and "Create, rename and delete a file" in r.text and "b-FAIL" not in r.text
    r = admin.post("/admin/folders", data={"name": "Bad", "path": "relative/path"})
    assert "full path" in r.text


def test_cli_create_admin_and_backup(tmp_path, monkeypatch, capsys):
    import io

    from sanger_seq_analyzer.cli import main
    data = tmp_path / "data"
    monkeypatch.setattr("sys.stdin", io.StringIO(ADMIN_PW + "\n"))
    assert main(["server", "--data", str(data), "create-admin", "--username", "boss", "--password-stdin"]) == 0
    assert main(["server", "--data", str(data), "add-user", "--username", "tech1", "--role", "viewer"]) == 0
    assert "Temporary password" in capsys.readouterr().out
    assert main(["server", "--data", str(data), "check"]) == 0
    assert main(["server", "--data", str(data), "backup", "--out", str(tmp_path / "b.zip")]) == 0
    with zipfile.ZipFile(tmp_path / "b.zip") as z:
        assert "sanger_seq.db" in z.namelist() and "config.toml" in z.namelist()
    from sanger_seq_analyzer.server import config
    assert backup(config.load(data)).is_file()
