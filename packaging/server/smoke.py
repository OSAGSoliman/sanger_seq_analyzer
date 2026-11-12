"""Smoke test of a running server, using only the standard library (CI and after installing).

    python packaging/server/smoke.py --base http://127.0.0.1:8443 --user admin --password-env SSA_PW \
        --examples examples

Signs in, uploads the example .ab1 files, runs them, downloads the export and checks that
consensus.fasta has the 3 PASS samples. Exit code 0 when everything worked.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import io
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path


class Client:
    def __init__(self, base: str, insecure: bool):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()]
        if insecure:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)
        self.csrf = ""

    def request(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None):
        h = {"Origin": self.base, **(headers or {})}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=120) as r:
                return r.status, r.headers, r.read()  # headers: case-insensitive
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def form(self, path: str, fields: dict, headers: dict | None = None):
        body = urllib.parse.urlencode(fields, doseq=True).encode()
        return self.request("POST", path, body, {"Content-Type": "application/x-www-form-urlencoded",
                                                  **(headers or {})})

    def upload(self, path: str, files: list[Path]):
        boundary = uuid.uuid4().hex
        out = io.BytesIO()
        for f in files:
            out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{f.name}\"\r\n"
                      "Content-Type: application/octet-stream\r\n\r\n".encode())
            out.write(f.read_bytes())
            out.write(b"\r\n")
        out.write(f"--{boundary}--\r\n".encode())
        return self.request("POST", path, out.getvalue(), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check(ok: bool, message: str) -> None:
    print(("ok   " if ok else "FAIL ") + message, flush=True)
    if not ok:
        raise SystemExit(1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password-env", default="SSA_PW", help="environment variable holding the password")
    p.add_argument("--examples", default="examples")
    p.add_argument("--insecure", action="store_true", help="accept a self-signed certificate")
    p.add_argument("--wait", type=int, default=60, help="seconds to wait for the server to answer")
    args = p.parse_args(argv)
    c = Client(args.base, args.insecure)

    deadline = time.time() + args.wait
    status = 0
    while time.time() < deadline:
        try:
            status, _h, body = c.request("GET", "/healthz")
            if status == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    check(status == 200, f"server answers at {args.base}/healthz")

    status, headers, _ = c.form("/login", {"username": args.user, "password": os.environ[args.password_env],
                                          "next": "/runs"})
    check(status == 303 and headers.get("location") == "/runs", "sign in")
    status, _h, body = c.request("GET", "/account/password")
    m = re.search(rb'name="csrf" value="([^"]+)"', body)
    check(status == 200 and m is not None, "session and CSRF token")
    c.csrf = m.group(1).decode()

    files = sorted(Path(args.examples).rglob("*.ab1"))
    status, _h, body = c.upload("/runs/draft", files)
    m = re.search(rb'name="draft" value="([^"]+)"', body)
    check(status == 200 and m is not None and b"11</b> samples" in body, f"upload {len(files)} .ab1 files and pair them")
    status, headers, body = c.form("/runs", {"draft": m.group(1).decode(), "name": "smoke test",
                                             "reference": "measles-n450"}, {"HX-Request": "true"})
    check(status == 204 and headers.get("HX-Redirect"), f"start the run ({status} {body[:200]!r})")
    run_path = headers["HX-Redirect"]

    for _ in range(120):
        status, headers, body = c.request("GET", run_path + "/status", headers={"HX-Request": "true"})
        if headers.get("HX-Refresh"):
            break
        time.sleep(1)
    status, _h, body = c.request("GET", run_path)
    check(b"3 PASS" in body and b"8 FAIL" in body, "run finished: 3 PASS, 8 FAIL")

    status, headers, body = c.form(run_path + "/export", {"scope": "all", "files": ["consensus", "html"]})
    check(status == 200 and headers.get("content-type") == "application/zip", "download the export")
    z = zipfile.ZipFile(io.BytesIO(body))
    fasta = z.read("consensus.fasta").decode()
    check(fasta.count(">") == 3 and "report.html" in z.namelist(), "consensus.fasta has the 3 PASS samples")
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
