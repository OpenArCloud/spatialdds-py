"""Shared gate plumbing: the state, and an in-process gateway on port 0.

In-process rather than a subprocess: a gate that spawns a server has to poll
for readiness, and a readiness probe that fetches a URL until it answers cannot
tell the server it started from one already listening on that port. `aiohttp`
hands back a bound port only once the socket is listening, so there is nothing
to poll and nothing to confuse.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# The gateway lives in the distribution under lib/spatialdds; a gate runs
# from a checkout, so the checkout's copy goes on the path rather than an
# installed one.
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from aiohttp import web                                        # noqa: E402

from spatialdds.server import gateway, gw_http                    # noqa: E402

#: The vendored generated artifacts -- serializer tables, JSON Schemas,
#: pinned IDL -- which travel inside the distribution so that a pip
#: install can stand up a conformant endpoint. One copy, named here so
#: that a gate reading them never hardcodes a second opinion about where
#: they are.
ARTIFACTS = ROOT / "lib" / "spatialdds" / "spatialdds" / "artifacts"

MCAP = ROOT / "samples" / "queuing-retail-sample.mcap"
FOUNTAIN = ROOT / "samples" / "fountain-anchors.mcap"
MANIFESTS = ROOT / "samples" / "manifests"
ASSETS = ROOT / "samples" / "assets"
SUMMARY = ROOT / "samples" / "queuing-retail-sample.summary.json"
TOKEN = "gate-token-not-a-secret"


def summary() -> dict:
    """The recording's own summary: per-topic counts and the content digest.

    This is where every expected count in these gates comes from. It travels
    with the recording and was written when the recording was cut, so it is
    independent of this repository: asking the gateway how many samples it
    found and then checking it served that many would pass for any gateway,
    including a broken one.
    """
    return json.loads(SUMMARY.read_text())


def state(path: Path = MCAP,
          manifests: Path | None = MANIFESTS) -> gateway.State:
    return gateway.build_state(path, manifests)


def blank() -> gateway.State:
    """An empty registry: no recording, types from the IDL pinned in the
    package, nothing held and nothing to serve until something is published.

    The state 0.2.0 made the default, and the one a gate has to be able to ask
    for by name now that three of them start from it.
    """
    return gateway.build_state(None, None)


class Gateway:
    """An in-process gateway, optionally answering as another authority.

    `advertise` sets the base the endpoint believes it is reached at, which is
    what §7.5.2 resolution compares a URI's authority against. It is harness
    wiring and nothing else: the socket is still on loopback, so a gate that
    follows an advertised `https_base` has to put the connect host back. That
    substitution is the one thing a resolution gate may do and a client may
    not, and `connect()` is the only place it happens.
    """

    def __init__(self, st: gateway.State, token: str = TOKEN,
                 advertise: str | None = None, publish: bool = False,
                 journal: Path | None = None,
                 commands: "object | None" = None,
                 live: bool | None = None) -> None:
        self.state = st
        self.token = token
        self.advertise = advertise
        self.publish = publish
        self.journal_path = journal
        self.journal = None
        #: An N.6 command registry, or None for an endpoint with no verbs --
        #: which is the normal state and the one every other gate wants.
        self.commands = commands
        #: N.5 socket lifetime, independent of the write surface since 0.5.0.
        #: None follows `publish`, which is what every gate but the live one
        #: wants.
        self.live = live
        self._runner: web.AppRunner | None = None
        self.base = ""
        #: The app itself, for a gate that acts as an embedder: `notify`
        #: takes the app, because that is what a live backend has.
        self.app: web.Application | None = None

    async def start(self) -> "Gateway":
        if self.journal_path is not None:
            from spatialdds.server import publish as _pub
            self.journal = _pub.Journal(self.journal_path, self.state)
            self.journal.replay()
        app = gw_http.make_app(self.state, "http://127.0.0.1", self.token,
                               self.publish, self.journal, ASSETS,
                               command_registry=self.commands,
                               live_subscriptions=self.live)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore
        self.base = f"http://127.0.0.1:{port}"
        app["base"] = self.advertise.rstrip("/") if self.advertise \
            else self.base
        self.app = app
        return self

    def connect(self, advertised_url: str) -> str:
        """An advertised URL, pointed back at the loopback socket serving it."""
        adv = (self.advertise or self.base).rstrip("/")
        assert advertised_url.startswith(adv), \
            f"{advertised_url!r} was not advertised by {adv!r}"
        return self.base + advertised_url[len(adv):]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def __aenter__(self) -> "Gateway":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()


def report(title: str, checks: list[tuple[str, bool, str]], verdict: str) -> int:
    print(title + "\n")
    for label, passed, detail in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
        if not passed and detail:
            print(f"       {detail}")
    ok = all(c[1] for c in checks)
    print(f"\n{'PASS' if ok else 'FAIL'}, {verdict}")
    return 0 if ok else 1
