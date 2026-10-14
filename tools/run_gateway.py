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

from spatialdds_web import gateway, gw_http, publish           # noqa: E402

DEFAULT_MCAP = ROOT / "samples" / "queuing-retail-sample.mcap"
DEFAULT_MANIFESTS = ROOT / "samples" / "manifests"
DEFAULT_ASSETS = ROOT / "samples" / "assets"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcap", type=Path, default=DEFAULT_MCAP,
                    help="a typed SpatialDDS 1.8 MCAP with embedded omgidl "
                         "schemas. Optional once --journal is given: a "
                         "journal carries its own types.")
    ap.add_argument("--no-mcap", action="store_true",
                    help="start with no seed recording at all")
    ap.add_argument("--publish", action="store_true",
                    help="enable the implementation-defined write surface "
                         "(PUT/DELETE on instance URLs). Off by default; "
                         "operator-scoped when on, so --token applies.")
    ap.add_argument("--journal", type=Path, default=None,
                    help="an MCAP journal of accepted publishes. Replayed "
                         "after the seed on startup and appended to as "
                         "records are accepted, so restart is the same state.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS,
                    help="a directory of content manifests this endpoint is "
                         "the authority for; each is keyed by the full "
                         "spatialdds:// URI in its own `id`. Resolution "
                         "(§7.5.2) is offered only for what is held here, and "
                         "only when --public-url names their authority.")
    ap.add_argument("--assets", type=Path, default=DEFAULT_ASSETS,
                    help="a directory of asset bytes served read-only at "
                         "/assets/<name>. Implementation-defined: Appendix N "
                         "defines no asset route, and a manifest's "
                         "assets[].uri is an ordinary URL that usually points "
                         "elsewhere. Here so the walkthrough can be followed "
                         "without a push.")
    ap.add_argument("--public-url", default=None,
                    help="the URL clients reach this endpoint at, when that "
                         "differs from --host:--port -- behind a reverse "
                         "proxy or TLS terminator it always does. Every URL "
                         "in a service manifest is built from it.")
    ap.add_argument("--token", default=None,
                    help="bearer token for operator-scoped resources; "
                         "generated if not given")
    a = ap.parse_args()

    seed = None if a.no_mcap else a.mcap
    if seed is not None and not seed.is_file():
        raise SystemExit(f"no such recording: {seed}")
    token = a.token or secrets.token_urlsafe(24)
    st = gateway.build_state(
        seed, a.manifests if a.manifests and a.manifests.is_dir() else None)
    # The manifest hands clients absolute URLs, so the base has to be the
    # address a client can reach rather than the socket this process bound.
    # Behind a TLS terminator those differ, and the failure is quiet: the
    # endpoint answers, the manifest it serves points at 0.0.0.0, and every
    # path a client follows out of it is unreachable.
    base = a.public_url.rstrip("/") if a.public_url else \
        f"http://{a.host}:{a.port}"

    print(f"recording     {seed if seed else '(none: empty registry)'}")
    if seed is not None:
        print(f"              sha256 {st.source_sha256}")
    print(f"serving       {base}"
          + (f"  (bound {a.host}:{a.port})" if a.public_url else ""))
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
    meta = gateway.resolver_metadata(st, base)
    if meta:
        print(f"\n  authority   {meta['authority']}, "
              f"{len(st.manifests)} manifest(s) resolvable")
    elif st.manifests:
        # The misconfiguration worth saying plainly rather than leaving as a
        # 404: the manifests are held, and the endpoint is not reachable under
        # the name their URIs resolve against.
        auth = sorted({gateway.uri_authority(u) for u in st.manifests})
        print(f"\n  authority   none: {len(st.manifests)} manifest(s) held "
              f"for {', '.join(auth)}, but this endpoint answers as "
              f"{gateway.base_authority(base)!r}; pass --public-url to serve "
              f"resolution")
    print(f"\n  discovery   {base}/.well-known/spatialdds/search")
    print(f"  schemas     {base}/.well-known/spatialdds/schemas")
    if meta:
        print(f"  resolver    {base}/.well-known/spatialdds/resolver")
        print(f"  resolve     {meta['https_base']}?uri=<urlencoded>")

    # Flush before handing the process to the event loop. Python
    # block-buffers stdout when it is not a terminal, so under `docker logs`
    # or a redirect every line above would sit in the buffer until the
    # process exited -- a startup banner that appears only after shutdown.
    sys.stdout.flush()

    jr = None
    if a.journal is not None:
        jr = publish.Journal(a.journal, st)
        n = jr.replay()
        print(f"  journal     {a.journal}, {n} entry(ies) replayed")
    if a.publish:
        # Defensive, and unreachable by construction: a bound endpoint always
        # has a host, so with no --public-url the authority is `host:port`.
        # That is a coherent namespace rather than an absent one, which is why
        # the warning below matters more than this guard -- publishing against
        # a loopback authority works and is almost never what was meant.
        if not gateway.base_authority(base):
            raise SystemExit(
                "--publish needs an authority to accept records into, and "
                "this endpoint has none. Pass --public-url.")
        if not a.public_url:
            print(f"  WARNING     publishing as authority "
                  f"{gateway.base_authority(base)!r}; records must name that "
                  f"authority in every URI. Pass --public-url to publish into "
                  f"a real namespace.")
        if st.model is None:
            raise SystemExit(
                "--publish needs the types, and this endpoint learned none. "
                "Seed it with --mcap or --journal; a journal carries its own "
                "IDL.")
        print(f"  publish     ON, operator-scoped, journal "
              f"{'on' if jr else 'OFF (in memory only)'}")
    else:
        print("  publish     off (read-only; --publish enables it)")

    try:
        web.run_app(gw_http.make_app(st, base, token, a.publish, jr,
                                     a.assets if a.assets
                                     and a.assets.is_dir() else None),
                    host=a.host,
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
