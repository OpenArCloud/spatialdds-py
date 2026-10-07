#!/usr/bin/env python3
"""Serve a typed SpatialDDS MCAP over the web binding. No DDS in the path.

    python3 tools/run_gateway.py [--mcap samples/queuing-retail-sample.mcap]

The recording is the input and the only one. Its embedded IDL describes the
wire, its `@key` annotations name the instances, its topic names give the
resource paths. A client with `fetch` and a WebSocket then reads typed
SpatialDDS without a DDS library, a domain, or a discovery handshake.

Operator-scoped resources need the bearer token printed at startup. Public
resources -- a site's zones, lines, frames and georeference -- need nothing.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiohttp import web                                        # noqa: E402

from spatialdds_web import gateway, gw_http                    # noqa: E402

DEFAULT_MCAP = ROOT / "samples" / "queuing-retail-sample.mcap"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcap", type=Path, default=DEFAULT_MCAP,
                    help="a typed SpatialDDS 1.8 MCAP with embedded omgidl "
                         "schemas")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--token", default=None,
                    help="bearer token for operator-scoped resources; "
                         "generated if not given")
    a = ap.parse_args()

    if not a.mcap.is_file():
        raise SystemExit(f"no such recording: {a.mcap}")
    token = a.token or secrets.token_urlsafe(24)
    st = gateway.build_state(a.mcap)
    base = f"http://{a.host}:{a.port}"

    print(f"recording     {a.mcap}")
    print(f"              sha256 {st.source_sha256}")
    print(f"serving       {base}")
    print(f"bearer token  {token}\n")
    n_l = sum(len(v) for v in st.latched.values())
    n_e = sum(len(v) for v in st.live.values())
    print(f"  {len(st.resources)} resource(s) in {len(st.scenes)} domain(s), "
          f"{n_l} latched instance(s), {n_e} live event(s)\n")
    for path in sorted(st.resources):
        res = st.resources[path]
        print(f"  {res.access:17s} {path}")
        print(f"  {'':17s}   {len(st.latched_initial.get(res.topic, {})):4d} "
              f"replayed, {len(st.latched.get(res.topic, {})):4d} current, "
              f"{len(st.live.get(res.topic, [])):6d} live")
    print(f"\n  discovery   {base}/.well-known/spatialdds/search")
    print(f"  schemas     {base}/.well-known/spatialdds/schemas")

    try:
        web.run_app(gw_http.make_app(st, base, token), host=a.host,
                    port=a.port, print=None)
    except OSError as e:
        # An already-bound port is the one failure where everything downstream
        # still looks healthy: a client connects, a readiness probe succeeds,
        # and both are talking to whatever was already listening there.
        if e.errno in (48, 98):
            raise SystemExit(
                f"port {a.port} is already in use on {a.host}. Something else "
                f"is listening there, possibly a gateway from an earlier run: "
                f"a client would reach that one instead of this one and the "
                f"difference would not be obvious. Stop it, or pass --port.")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
