#!/usr/bin/env python3
"""The security sweep: names that try to escape, strings that try to inject.

* **Asset names** that climb (`..`, encoded and double-encoded, backslashes,
  NUL, absolute paths) are refused and never answered with a byte of any file
  outside the assets directory -- checked against the content of files that
  would be reachable if the route resolved them.
* **Instance keys** that look like paths stay keys: a write aimed through
  `..` segments or encoded slashes at another lane lands nowhere in that
  lane, and a key with a control character is refused.
* **Echoed strings** -- `reason=` on a withdrawal -- carrying CR/LF cannot
  add a response header or a log line, and arrive on the stream as the same
  literal string.
* **Unmatched routes** are problem documents, not a framework's plain text.
* **Token comparison is constant-time**: `authenticate` compares against
  every row whether the token matches the first row, the last, or none.

Plain HTTP at the TLS front is checked against the live endpoint in the
deploy report, not here: there is no Caddy in process.

    gates/security.py
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sys
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

from harness import FOUNTAIN, MANIFESTS, Gateway, report, state  # noqa: E402
from publish_common import resolve_path                       # noqa: E402

from spatialdds.server import tenancy                          # noqa: E402

AUTH = "demo.spatialdds.org"
L1, L2 = "ut-austin-south-mall", "wow-world"
DUCK = json.loads((ROOT / "samples" / "duck.input.json").read_text())
#: Bytes that would come back if an asset name escaped its directory.
SECRETS = [p.read_bytes()[:64] for p in (
    ROOT / "README.md", ROOT / "samples" / "duck.input.json",
    ROOT / "lib" / "spatialdds" / "spatialdds" / "server" / "gw_http.py")]
CLIMBS = ["..", "../README.md", "%2e%2e", "%2e%2e%2fREADME.md",
          "..%2F..%2FREADME.md", "%252e%252e%252fREADME.md",
          "..%5C..%5CREADME.md", "duck.gltf%00.txt", "%2Fetc%2Fpasswd",
          "....//....//README.md", "%c0%ae%c0%ae%2fREADME.md"]


class Lines(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.extend(record.getMessage().splitlines())


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    t1, t2 = tenancy.issue(), tenancy.issue()
    table = tenancy.TokenTable([
        (tenancy.digest(t1), tenancy.Principal("james", "o.org",
                                               frozenset({L1}))),
        (tenancy.digest(t2), tenancy.Principal("wow", "w.example",
                                               frozenset({L2})))])
    access = Lines()
    errors = Lines()
    logging.getLogger("aiohttp.server").addHandler(errors)
    logging.getLogger("aiohttp.web").addHandler(errors)
    logging.getLogger("aiohttp.access").addHandler(access)
    logging.getLogger("aiohttp.access").setLevel(logging.INFO)
    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=f"https://{AUTH}",
                       publish=True, tokens=table).start()
    try:
        async with aiohttp.ClientSession() as s:
            b = gw.base
            async with s.get(b + "/assets/duck.gltf") as r:
                good = r.status
            leaks, codes = [], []
            for name in CLIMBS:
                async with s.get(b + "/assets/" + name) as r:
                    body = await r.read()
                    codes.append(r.status)
                    if (r.status == 200 or r.status >= 500
                            or any(x in body for x in SECRETS)):
                        leaks.append((name, r.status))
            checks.append((f"{len(CLIMBS)} climbing asset names are refused and "
                           f"return no byte of a file outside assets/ (a real "
                           f"asset still serves)",
                           good == 200 and not leaks,
                           f"leaks={leaks} codes={codes}"))

            # -- keys that look like paths, from lane 2 at lane 1 --
            lane1 = b + f"/{L1}/content/content_announce/v1"
            async with s.get(lane1) as r:
                before = await r.read()
            rec = dict(DUCK["announce"]["record"])
            tries = [f"..%2F..%2F{L1}%2Fcontent%2Fcontent_announce%2Fv1%2Fx",
                     f"%2e%2e%2f{L1}", "..", "%252F..%252F" + L1]
            got = []
            for raw in tries:
                from urllib.parse import unquote
                rec["content_id"] = unquote(raw)
                async with s.put(b + f"/{L2}/content/content_announce/v1/{raw}",
                                 json=rec,
                                 headers={"Authorization": f"Bearer {t2}"}) as r:
                    got.append(r.status)
            async with s.get(lane1) as r:
                after = await r.read()
            checks.append(("keys aimed through `..` and encoded slashes at "
                           "another lane change nothing in that lane",
                           before == after, f"statuses {got}"))
            async with s.put(b + f"/{L1}/content/content_announce/v1/a%00b",
                             json={**rec, "content_id": "a\x00b"},
                             headers={"Authorization": f"Bearer {t1}"}) as r:
                nul, nb = r.status, await r.read()
            checks.append(("a key with a NUL is a 400 bad-key problem document",
                           nul == 400 and b"bad-key" in nb, f"{nul} {nb[:90]!r}"))

            # -- reason= with CR/LF: no header, no log line, literal on the wire
            m = dict(DUCK["manifest"])
            m["id"] = f"spatialdds://{AUTH}/{L1}/content/inject"
            hdr = {"Authorization": f"Bearer {t1}"}
            rec = dict(DUCK["announce"]["record"])
            rec["content_id"] = "content/inject"
            rec["manifest_uri"] = m["id"]
            p = f"/{L1}/content/content_announce/v1/" + quote("content/inject",
                                                             safe="")
            async with s.put(b + p, json=rec, headers=hdr) as r:
                assert r.status == 201, r.status
            ws = await s.ws_connect(lane1)
            while json.loads((await ws.receive()).data)["event"] != \
                    "end_of_replay":
                pass
            evil = "done\r\nX-Injected: 1\r\n\r\n<script>"
            async with s.delete(b + p + "?reason=" + quote(evil, safe=""),
                                headers=hdr) as r:
                dh = r.headers
            ev = None
            for _ in range(10):
                e = json.loads((await ws.receive(timeout=5)).data)
                if e["event"] == "dispose":
                    ev = e
                    break
            await ws.close()
            checks.append(("CR/LF in `reason=` adds no response header and "
                           "arrives on the stream as the same literal string",
                           "X-Injected" not in dh and ev is not None
                           and ev["reason"] == evil, f"{ev and ev['reason']!r}"))
            await asyncio.sleep(0.1)
            injected = [ln for ln in access.lines
                        if ln.lstrip().startswith("X-Injected")]
            checks.append(("…and no access-log line begins with the injected "
                           "header", not injected and len(access.lines) > 0,
                           f"{injected[:2]} ({len(access.lines)} lines seen)"))

            # -- the router's own refusals are problem documents --
            outs = []
            for verb, path in (("GET", "/no/such/route/at/all/here"),
                               ("PATCH", f"/{L1}/content/content_announce/v1/x"),
                               ("GET", "/.well-known/spatialdds/nothing")):
                async with s.request(verb, b + path) as r:
                    outs.append((verb, r.status,
                                 r.headers.get("Content-Type", "")))
            checks.append(("unmatched routes and methods are problem documents",
                           all(c >= 400 and ct.startswith(
                               "application/problem+json") for _v, c, ct in outs),
                           f"{outs}"))
    finally:
        await gw.stop()

    checks.append(("no error or traceback in the gateway's log for any of it",
                   not errors.lines, f"{errors.lines[:2]}"))

    # -- constant-time token comparison --
    rows = [(tenancy.digest(f"t{i}"), tenancy.Principal(f"p{i}", "", frozenset(
        {"s"}))) for i in range(5)]
    tt = tenancy.TokenTable(rows)
    counts = {}
    for label, tok in (("first", "t0"), ("last", "t4"), ("none", "nope")):
        with mock.patch.object(tenancy.hmac, "compare_digest",
                               wraps=hmac.compare_digest) as cd:
            tt.authenticate(f"Bearer {tok}")
            counts[label] = cd.call_count
    checks.append(("authentication compares every row, whichever matches "
                   "(first, last, none)",
                   set(counts.values()) == {len(rows)}, f"{counts}"))
    return report("security", checks,
                  "names cannot escape, strings cannot inject, refusals are "
                  "documents, and token checks take constant work")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
