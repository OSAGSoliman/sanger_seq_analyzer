"""Fixtures for the web server tests: a fresh data folder, an app, and signed-in clients."""

import re
from concurrent.futures import Future

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from sanger_seq_analyzer.server import config  # noqa: E402
from sanger_seq_analyzer.server.app import create_app  # noqa: E402

ADMIN_PW = "correct horse battery"
CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


class InlineExecutor:
    """Runs submitted jobs at once in the caller (deterministic tests)."""

    def submit(self, fn, *args, **kwargs):
        f = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 (handed to the future like a real executor)
            f.set_exception(exc)
        return f

    def shutdown(self, wait=True, cancel_futures=False):
        pass


@pytest.fixture
def cfg(tmp_path):
    return config.load(tmp_path / "data")


@pytest.fixture
def app(cfg):
    return create_app(cfg, executor=InlineExecutor())


@pytest.fixture
def accounts(app):
    return app.state.ctx.accounts


def new_client(app):
    return TestClient(app, base_url="http://testserver", follow_redirects=False)


def sign_in(client, username, password):
    r = client.post("/login", data={"username": username, "password": password, "next": "/"})
    assert r.status_code == 303, r.text
    return r


def csrf_of(client, path="/account/password"):
    r = client.get(path)
    m = CSRF_RE.search(r.text)
    assert m, r.text[:500]
    return m.group(1)


class Browser:
    """A signed-in TestClient that adds the CSRF token to every POST, like the pages do."""

    def __init__(self, app, username, password):
        self.client = new_client(app)
        with self.client:
            pass
        sign_in(self.client, username, password)
        self.csrf = csrf_of(self.client)

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, files=None, headers=None, **kw):
        h = {"X-CSRF-Token": self.csrf, **(headers or {})}
        return self.client.post(url, data=data, files=files, headers=h, **kw)


@pytest.fixture
def admin(app, accounts):
    accounts.create("admin", "Lab Admin", "admin", ADMIN_PW, must_change=False)
    return Browser(app, "admin", ADMIN_PW)


@pytest.fixture
def make_user(app, accounts):
    def make(username, role, password="a long enough password"):
        accounts.create(username, username.title(), role, password, must_change=False)
        return Browser(app, username, password)
    return make
