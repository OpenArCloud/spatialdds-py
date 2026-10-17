"""Stand up a SpatialDDS registry. No DDS in the path, no clone needed.

    pip install "spatialdds[server]"
    python -m spatialdds.serve --authority registry.mytown.example --publish

That is a working registry: empty, typed, and ready to be published into. The
types come from the IDL pinned inside this package, so there is no "required
base recording" and nothing to download first -- a registry's state is its
journal, and its types are the specification it implements.

    python -m spatialdds.serve --demo

serves the packaged fountain set instead -- five anchors, two content
manifests, three assets -- with publishing on and a token printed. It is the
same boot path with seed data on top, not a separate mode.

This is the reference gateway: one implementation, living in the distribution
so that a pip install is enough. The repository's `tools/run_gateway.py` is a
shim over this module and adds nothing.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

#: The packaged fountain set, byte-identical to the repository's `samples/`
#: copy (`tools/pack_demo.py`, gated).
DEMO = Path(__file__).resolve().parent / "demo"

MISSING_SERVER = """this needs the server extra, which is not installed:

    pip install "spatialdds[server]"

The client half -- sd.connect(), search, publish against someone else's
endpoint -- needs only requests and websockets, so being an endpoint is the
part you opt into."""


def _server():
    """The server half, or one sentence saying how to get it."""
    try:
        from aiohttp import web
        from .server import commands, gateway, gw_http, pin, publish
    except ImportError as e:                                  # pragma: no cover
        raise SystemExit(f"spatialdds.serve: {MISSING_SERVER}\n\n({e})")
    return web, commands, gateway, gw_http, pin, publish


def build_parser(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap.add_argument("--demo", action="store_true",
                    help="serve the packaged fountain set with publishing on: "
                         "five surveyed anchors, two content manifests and "
                         "the assets they name. Seed data over the same boot "
                         "path, not a different mode.")
    ap.add_argument("--mcap", type=Path, default=None,
                    help="a typed SpatialDDS MCAP to seed from. Optional: "
                         "with no recording the registry starts empty and "
                         "takes its types from the IDL pinned in this package.")
    ap.add_argument("--authority", default=None,
                    help="the DNS name this registry answers as, e.g. "
                         "registry.mytown.example. Every URI it accepts and "
                         "every URL it advertises is built from it; §7.5.2 "
                         "resolution is offered only for this authority.")
    ap.add_argument("--public-url", default=None,
                    help="the full URL clients reach this endpoint at, when "
                         "that differs from --host:--port. Use --authority "
                         "for the ordinary case; this is for an unusual "
                         "scheme, port or path prefix.")
    ap.add_argument("--publish", action="store_true",
                    help="enable the write surface (PUT/DELETE on instance "
                         "URLs). Off by default; operator-scoped when on, so "
                         "--token applies.")
    ap.add_argument("--no-publish", action="store_true",
                    help="keep the write surface off even with --demo, which "
                         "otherwise turns it on. What a read-only deployment "
                         "of the demo data wants.")
    ap.add_argument("--journal", type=Path, default=None,
                    help="where accepted publishes are journalled. Defaults "
                         "to ./spatialdds-registry.mcap when --publish is on, "
                         "and the path is printed at startup. The journal is "
                         "an ordinary SpatialDDS recording: the registry's "
                         "state is replayable by any tool that reads one.")
    ap.add_argument("--no-journal", action="store_true",
                    help="publish in memory only, losing everything on exit")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--manifests", type=Path, default=None,
                    help="a directory of content manifests this endpoint is "
                         "the authority for, each keyed by the full "
                         "spatialdds:// URI in its own `id`")
    ap.add_argument("--assets", type=Path, default=None,
                    help="a directory of asset bytes served read-only at "
                         "/assets/<name>. Implementation-defined: Appendix N "
                         "defines no asset route, and a manifest's "
                         "assets[].uri is an ordinary URL that usually points "
                         "elsewhere.")
    ap.add_argument("--token", default=None,
                    help="bearer token for operator-scoped resources; "
                         "generated and printed if not given")
    return ap


def _demo_command(record: dict):
    """The `--demo` command handler: both of M.1's outcomes, no side effects.

    A reference handler that pretended to act would be worse than none: it
    would make the surface look wired to something. So this decides, says
    why, and changes nothing --- which is all a demo registry can honestly do,
    having no backend behind it.

    `method == "DECLINE_ME"` is declined on purpose. Without a reachable
    decline, the only exercised path would be acceptance, and M.1's whole
    point is that a refusal is a first-class observable outcome; a surface
    that can only say yes has not implemented it.
    """
    from .server.commands import Outcome      # the server half is optional
    if record.get("method") == "DECLINE_ME":
        return Outcome.decline(
            "the demo handler declines this method by arrangement, so a "
            "caller can see M.1's decline path without a backend: a refusal "
            "carries a reason and is not an absence")
    return Outcome.accept(
        "accepted by the --demo handler, which records nothing: this registry "
        "has no backend, and a handler that claimed to act would make the "
        "surface look wired to something it is not")


def main(argv: list[str] | None = None) -> int:
    web, commands, gateway, gw_http, pin, publish = _server()
    ap = build_parser(argparse.ArgumentParser(
        prog="python -m spatialdds.serve",
        description="Stand up a SpatialDDS registry."))
    a = ap.parse_args(argv)
    if a.no_publish:
        a.publish = False

    if a.authority and a.public_url:
        raise SystemExit(
            "--authority and --public-url say the same thing two ways. Pass "
            "--authority registry.example for the ordinary case, or "
            "--public-url https://registry.example/prefix for an unusual one.")

    # --demo is seed data over the default boot path: it fills in the three
    # paths and switches publishing on, and anything stated explicitly wins.
    if a.demo:
        if a.mcap is None:
            a.mcap = DEMO / "fountain-anchors.mcap"
        if a.manifests is None:
            a.manifests = DEMO / "manifests"
        if a.assets is None:
            a.assets = DEMO / "assets"
        a.publish = not a.no_publish
    if a.mcap is not None and not a.mcap.is_file():
        raise SystemExit(f"no such recording: {a.mcap}")

    # The N.6 demo handler. `--demo` exists so the surface is exercisable
    # without a backend, and a command surface nobody can POST to is a route
    # with no evidence behind it -- the conformance suite's first clause had
    # never been exercised by any run in this program precisely because there
    # was nothing to exercise it against.
    #
    # It accepts a `GeoAnchor` command and declines one verb, so both of M.1's
    # outcomes are reachable from a bare `--demo`: acceptance, and a decline
    # carrying a reason. It does nothing to the registry, because `--demo` has
    # no backend to do anything with -- which is the honest shape for a
    # reference handler and the reason its reason string says so.
    cmd_registry = commands.Registry()
    if a.demo:
        cmd_registry.register("spatial.core.GeoAnchor", _demo_command)

    token = a.token or secrets.token_urlsafe(24)
    st = gateway.build_state(
        a.mcap, a.manifests if a.manifests and a.manifests.is_dir() else None)
    # The manifest hands clients absolute URLs, so the base has to be the
    # address a client can reach rather than the socket this process bound.
    # Behind a TLS terminator those differ, and the failure is quiet: the
    # endpoint answers, the manifest it serves points somewhere unreachable,
    # and every path a client follows out of it dead-ends.
    if a.public_url:
        base = a.public_url.rstrip("/")
    elif a.authority:
        base = f"https://{a.authority}"
    else:
        base = f"http://{a.host}:{a.port}"
    advertised = bool(a.authority or a.public_url)

    journal_path = None
    if a.journal is not None:
        journal_path = a.journal
    elif a.publish and not a.no_journal and not a.demo:
        # A registry journals by default, because one that forgets on restart
        # is not a registry. A demo does not: it is somebody's first two
        # minutes with this, and it should not leave an MCAP in whatever
        # directory they happened to be standing in. `--journal PATH` with
        # `--demo` opts in.
        journal_path = Path("spatialdds-registry.mcap")

    print(f"recording     {a.mcap if a.mcap else '(none: empty registry)'}")
    if a.mcap is not None:
        print(f"              sha256 {st.source_sha256}")
    print(f"types         {st.type_source}")
    print(f"              SpatialDDS-spec {_spec_commit()}")
    print(f"serving       {base}"
          + (f"  (bound {a.host}:{a.port})" if advertised else ""))
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
    if not st.resources:
        print("  (no resources yet: an empty registry has none until "
              "something is published)")
    meta = gateway.resolver_metadata(st, base)
    if meta:
        print(f"\n  authority   {meta['authority']}, "
              f"{len(st.manifests)} manifest(s) resolvable")
    elif st.manifests and not (a.demo and not advertised):
        # The misconfiguration worth saying plainly rather than leaving as a
        # 404: the manifests are held, and the endpoint is not reachable under
        # the name their URIs resolve against. Skipped for a bare --demo,
        # where it is not a misconfiguration but a deliberate choice, and the
        # note further down says which line gets the other half.
        auth = sorted({gateway.uri_authority(u) for u in st.manifests})
        print(f"\n  authority   none: {len(st.manifests)} manifest(s) held "
              f"for {', '.join(auth)}, but this endpoint answers as "
              f"{gateway.base_authority(base)!r}; pass --authority to serve "
              f"resolution")
    else:
        print(f"\n  authority   {gateway.base_authority(base)}")
    # The lines a person needs in their second terminal, ready to paste. The
    # REACHABLE address, not the advertised one: --authority sets what the
    # manifests advertise, which is frequently a name the reader's machine
    # cannot resolve, and pasting that hands them a variable that fails on
    # first use.
    reach = f"http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}"
    print("\n  paste this into the second terminal:\n")
    print(f"    export BASE={reach}")
    print(f"    export SPATIALDDS_ENDPOINT={reach}")
    if a.publish:
        print(f"    export TOK={token}")
        print(f"    export SPATIALDDS_TOKEN={token}")
    if advertised and gateway.base_authority(base) != reach.split("//")[1]:
        print(f"\n  note  this endpoint advertises {base} and is reachable at "
              f"{reach}. A client connects to the reachable address and "
              f"follows\n        the advertised one by path, which the "
              f"spatialdds client library does for you.")

    if a.demo and not advertised:
        # One endpoint cannot both advertise a DNS name and be followed by
        # URL from a laptop, and the demo picks the half that keeps every
        # example working: it advertises the address it is reached at, so an
        # absolute URL out of a service manifest can be followed verbatim.
        # The packaged manifests are keyed under demo.spatialdds.org, so
        # resolution is the half that needs the other configuration, and
        # saying which line gets it is cheaper than a 404 nobody expected.
        print("\n  note  a URI belongs to the authority it names, and the "
              "packaged manifests are keyed\n        under "
              "demo.spatialdds.org -- so resolution (§7.5.2), and any publish "
              "of a record\n        naming that authority, want the other "
              "configuration:\n"
              "          python -m spatialdds.serve --demo --authority "
              "demo.spatialdds.org\n"
              "        As served now, every absolute URL this endpoint hands "
              "out can be followed\n        verbatim, which is what the "
              "reading examples do.")
    print(f"\n  discovery   {base}/.well-known/spatialdds/search")
    print(f"  schemas     {base}/.well-known/spatialdds/schemas")
    if meta:
        print(f"  resolver    {base}/.well-known/spatialdds/resolver")
        print(f"  resolve     {meta['https_base']}?uri=<urlencoded>")

    jr = None
    if journal_path is not None:
        jr = publish.Journal(journal_path, st)
        n = jr.replay()
        print(f"  journal     {journal_path}, {n} entry(ies) replayed")
    elif a.publish:
        print(f"  journal     none{' (--demo)' if a.demo else ' (--no-journal)'}"
              f": publishes live in memory and die with this process"
              + (", so this leaves nothing behind" if a.demo else ""))
    if a.publish:
        # Defensive, and unreachable by construction: a bound endpoint always
        # has a host, so with no --authority the authority is `host:port`.
        if not gateway.base_authority(base):
            raise SystemExit(
                "--publish needs an authority to accept records into, and "
                "this endpoint has none. Pass --authority.")
        if not advertised:
            print(f"  WARNING     publishing as authority "
                  f"{gateway.base_authority(base)!r}; records must name that "
                  f"authority in every URI. Pass --authority to publish into "
                  f"a real namespace.")
        if st.model is None:
            raise SystemExit(
                f"--publish needs the types, and this endpoint learned none. "
                f"It looked for the IDL pinned in this package, under "
                f"{pin.IDL_DIR} -- expected "
                f"{', '.join(sorted({f for fs in pin.CLOSURES.values() for f in fs}))} "
                f"-- and found no usable set there. That is a broken install "
                f"rather than a missing option; reinstall the package, or "
                f"seed from a recording with --mcap, whose embedded IDL "
                f"carries its own types.")
        print(f"  publish     ON, operator-scoped, journal "
              f"{'on' if jr else 'OFF (in memory only)'}")
    else:
        print("  publish     off (read-only; --publish enables it)")

    # Flush before handing the process to the event loop, and here rather
    # than earlier: Python block-buffers stdout when it is not a terminal, so
    # under `docker logs` or a redirect every line above sits in the buffer
    # until the process exits. The first version of this flushed before the
    # journal and publish lines were printed, which hid exactly the two lines
    # an operator starting a registry needs to see.
    sys.stdout.flush()

    try:
        web.run_app(gw_http.make_app(st, base, token, a.publish, jr,
                                     a.assets if a.assets
                                     and a.assets.is_dir() else None,
                                     command_registry=cmd_registry),
                    host=a.host, port=a.port, print=None)
    except OSError as e:
        # An already-bound port is the one failure where everything downstream
        # still looks healthy: a client connects, a readiness probe succeeds,
        # and both are talking to whatever was already listening there.
        if e.errno in (48, 98):
            raise SystemExit(
                f"port {a.port} is already in use on {a.host}. Something else "
                f"is listening there, possibly a registry from an earlier "
                f"run: a client would reach that one instead of this one and "
                f"the difference would not be obvious. Stop it, or pass "
                f"--port.")
        raise
    return 0


def _spec_commit() -> str:
    """The pin, read from the package rather than restated here."""
    import spatialdds
    return spatialdds.SPEC_COMMIT


if __name__ == "__main__":
    sys.exit(main())
