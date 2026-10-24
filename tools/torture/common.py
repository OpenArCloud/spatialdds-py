"""Shared plumbing for the torture runs: a real `spatialdds.serve` process.

Each run starts the gateway the way the deploy does -- `python -m
spatialdds.serve` with a token table and a journal -- as a subprocess it can
kill, and talks to it over HTTP. Nothing in-process: what is under test is the
service, its journal on a real filesystem, and what survives a SIGKILL.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AUTH = "demo.spatialdds.org"
SCENE = "ut-austin-south-mall"


def tokens(dirpath: Path) -> tuple[Path, str]:
    from spatialdds.server import tenancy
    tok = tenancy.issue()
    t = dirpath / "tokens.json"
    tenancy.TokenTable([(tenancy.digest(tok), tenancy.Principal(
        "torture", "openarcloud.org", frozenset({SCENE})))], t).save()
    return t, tok


class Service:
    def __init__(self, journal: Path, table: Path, port: int,
                 log: Path) -> None:
        self.journal, self.table, self.port, self.log = (journal, table, port,
                                                        log)
        self.base = f"http://127.0.0.1:{port}"
        self.p: subprocess.Popen | None = None

    def start(self) -> "Service":
        fh = open(self.log, "ab")
        self.p = subprocess.Popen(
            [sys.executable, "-m", "spatialdds.serve", "--demo", "--publish",
             "--authority", AUTH, "--host", "127.0.0.1", "--port",
             str(self.port), "--tokens", str(self.table), "--journal",
             str(self.journal), "--rate", "100000"],
            stdout=fh, stderr=subprocess.STDOUT)
        for _ in range(200):
            try:
                urllib.request.urlopen(self.base + "/.well-known/spatialdds/"
                                       "schemas", timeout=1)
                return self
            except (urllib.error.URLError, ConnectionError, OSError):
                if self.p.poll() is not None:
                    raise SystemExit(f"service exited {self.p.returncode}; "
                                     f"see {self.log}")
                time.sleep(0.05)
        raise SystemExit("service did not come up")

    def kill(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.send_signal(signal.SIGKILL)
            self.p.wait()

    def stop(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.terminate()
            self.p.wait()

    def rss_kb(self) -> int:
        with open(f"/proc/{self.p.pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
        return 0


def call(base: str, method: str, path: str, body: dict | None = None,
         tok: str | None = None, timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def manifest(name: str, pad: int = 0) -> dict:
    return {"id": f"spatialdds://{AUTH}/{SCENE}/content/{name}",
            "profile": "spatial.manifest/1.8", "rtype": "content",
            "content": {"content_id": f"content/{name}", "title": name,
                        "summary": "s" * pad}}


def snapshot(base: str, uris: list[str]) -> dict:
    from urllib.parse import quote
    out = {}
    for p in [f"/{SCENE}/content/content_announce/v1",
              f"/{SCENE}/frames/geo_anchor/v1",
              *[f"/spatialdds/resolve?uri={quote(u, safe='')}" for u in uris]]:
        code, _h, body = call(base, "GET", p)
        out[p] = (code, body)
    return out


def journal_valid(path: Path) -> tuple[bool, str]:
    """A complete MCAP: header, summary, footer -- readable by any MCAP tool."""
    from mcap.reader import make_reader
    try:
        with open(path, "rb") as f:
            s = make_reader(f).get_summary()
        return s is not None, (f"{s.statistics.message_count} msg, "
                               f"{s.statistics.metadata_count} meta"
                               if s else "no summary")
    except Exception as e:                                     # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def tracebacks(log: Path) -> int:
    return log.read_text(errors="replace").count("Traceback")


os.environ.setdefault("PYTHONUNBUFFERED", "1")
