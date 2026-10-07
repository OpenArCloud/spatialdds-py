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

from aiohttp import web                                        # noqa: E402

from spatialdds_web import gateway, gw_http                    # noqa: E402

MCAP = ROOT / "samples" / "queuing-retail-sample.mcap"
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


def state(path: Path = MCAP) -> gateway.State:
    return gateway.build_state(path)


class Gateway:
    def __init__(self, st: gateway.State, token: str = TOKEN) -> None:
        self.state = st
        self.token = token
        self._runner: web.AppRunner | None = None
        self.base = ""

    async def start(self) -> "Gateway":
        app = gw_http.make_app(self.state, "http://127.0.0.1", self.token)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore
        self.base = f"http://127.0.0.1:{port}"
        app["base"] = self.base
        return self

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
