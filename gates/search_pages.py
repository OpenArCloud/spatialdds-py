#!/usr/bin/env python3
"""`Endpoint.search()` reads every page a paging endpoint serves.

Finding 82: §3.3.0 pages a search with `next_page_token`, and `search()`
returned the first page's results and never asked for the next, so a client
of a paging endpoint (the demo's discovery binding is one) silently saw part
of the list. The reference registry never pages, so nothing here met it.

Against a stub endpoint on loopback:

  * three pages, one service each: `search()` returns all three, in order,
    and every request after the first carries the page token it was given and
    the same geohash and kind as the first;
  * `near()`, a helper over `search()`, sees all three too;
  * an endpoint that never stops paging: `search()` raises, naming the bound,
    rather than answering with part of the list.

Usage:  gates/search_pages.py
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                     # noqa: E402
import spatialdds as sd                                        # noqa: E402

PAGES = {"": ("svc-1", "p2"), "p2": ("svc-2", "p3"), "p3": ("svc-3", "")}


def manifest(sid: str) -> dict:
    return {"service": {"service_id": sid, "kind": "MAP", "topics": []},
            "coverage": [{"global": True}]}


class Stub(BaseHTTPRequestHandler):
    endless = False
    seen: list = []

    def do_GET(self):                                          # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        type(self).seen.append(q)
        if u.path != "/.well-known/spatialdds/search":
            self.send_response(404)
            self.end_headers()
            return
        token = q.get("page_token", "")
        if self.endless:
            n = int(token or 0) + 1
            body = {"results": [manifest(f"svc-{n}")],
                    "next_page_token": str(n)}
        else:
            sid, nxt = PAGES.get(token, ("", ""))
            body = {"results": [manifest(sid)] if sid else [],
                    "next_page_token": nxt}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def serve(endless: bool):
    handler = type("H", (Stub,), {"endless": endless, "seen": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, handler, f"http://127.0.0.1:{srv.server_address[1]}"


def main() -> int:
    checks: list = []
    srv, h, base = serve(endless=False)
    try:
        ep = sd.connect(base, lifted=False)
        got = [x.service_id for x in ep.search(geohash="9v6k", kinds=["MAP"])]
        asked = list(h.seen)
        h.seen.clear()
        near = [x.service_id for x in ep.near(30.2849, -97.7341, 50.0)]
    finally:
        srv.shutdown()
        srv.server_close()
    same = all(q.get("geohash") == "9v6k" and q.get("kind") == "MAP"
               for q in asked)
    tokens = [q.get("page_token", "") for q in asked]
    checks.append(("search() reads every page: three services from three "
                   "pages, in order (finding 82)",
                   got == ["svc-1", "svc-2", "svc-3"], f"got {got}"))
    checks.append(("…each request after the first carries the token it was "
                   "given, and every one the same geohash and kind",
                   tokens == ["", "p2", "p3"] and same,
                   f"requests {asked}"))
    checks.append(("near(), a helper over search(), sees every page too",
                   sorted(near) == ["svc-1", "svc-2", "svc-3"],
                   f"got {near}"))

    srv, h, base = serve(endless=True)
    try:
        ep = sd.connect(base, lifted=False)
        try:
            ep.search(max_pages=5)
            err = None
        except sd.SpatialDDSError as e:
            err = str(e)
        pages = len(h.seen)
    finally:
        srv.shutdown()
        srv.server_close()
    checks.append(("an endpoint that never stops paging: search() raises "
                   "after max_pages, naming the bound, rather than answer "
                   "with part of the list",
                   err is not None and "5 pages" in err and pages == 5,
                   f"raised {err!r} after {pages} request(s)"))
    return report("search pages", checks,
                  "search() reads every page, and says so when it cannot")


if __name__ == "__main__":
    sys.exit(main())
