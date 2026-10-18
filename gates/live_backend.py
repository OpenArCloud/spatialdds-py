#!/usr/bin/env python3
"""The two halves a live backend needs, and the negative that makes them two.

This gate is a probe promoted. It was written while embedding this gateway
against a live DDS bus in `SpatialDDS-demo`'s web bridge, and it found that
only one half of the contract was public: `publish.apply_sample` decided what
a new subscriber replays, and nothing public reached a subscriber already on
the socket. 0.5.0 added `gw_http.notify`; this holds both halves and the gap
between them.

**The negative is asserted, not implied.** `apply_sample` alone must reach a
new subscriber's replay and the collection GET, and must reach an open socket
*not at all*. If that stops being true, `notify` is no longer the second half
of anything and this gate's whole account of the pair is wrong --- which is
worth failing over, because the pair is what an embedder is told to call.

**And the flag split, likewise from both sides.** `live_subscriptions=True`
with `publish_enabled=False` must keep a subscription open *and* refuse a PUT:
that combination is the one an embedder with its own backend wants, and it was
unreachable before 0.5.0, when keeping subscribers connected meant exposing a
write surface. The old behaviour is checked too --- `publish_enabled=True`
alone still keeps sockets open --- because every existing caller passes only
that and none of them should have changed.

Usage:  gates/live_backend.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

import harness                                                 # noqa: E402
from spatialdds.server import gw_http, publish                 # noqa: E402

#: How long a wakened subscriber gets. Generous against a loaded machine; the
#: defect this gate guards was unbounded silence, not slowness.
PATIENCE = 4.0
#: How promptly a threaded `notify` must arrive. Tight on purpose: the
#: broken path delivered eventually, once something else woke the loop,
#: so a generous deadline passes over the defect this exists to catch.
THREAD_DEADLINE = 1.5
#: How long silence has to last to count as silence. The negative half of the
#: contract is a wait, so it costs this much wall clock and is worth it.
QUIET = 3.0


async def _drain_quiet(ws) -> int:
    """Everything already queued, until the socket goes quiet.

    A recording hands a new subscriber its replay and then its own live
    events. The first version of this probe stopped at `end_of_replay` and
    read one of those as if it were the injected sample -- a probe that
    cannot tell its signal from its subject's backlog measures nothing.
    """
    n = 0
    while True:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=2)
        except asyncio.TimeoutError:
            return n
        if msg.type is not aiohttp.WSMsgType.TEXT:
            return n
        n += 1


async def _one_event(ws, timeout: float):
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    if msg.type is not aiohttp.WSMsgType.TEXT:
        return None
    return json.loads(msg.data)


def _threaded_notify_check() -> tuple[str, bool, str]:
    """Does `notify` wake a parked subscriber from a foreign thread?

    **Not through the HTTP harness, and that is the point.** The in-process
    gateway shares one event loop with the gate's own client, so that loop is
    never idle --- every `await` in the gate drives it, and a foreign-thread
    wakeup that would otherwise be missed gets picked up immediately. A check
    written there passed identically with the defect present and absent,
    which is a check that measures nothing.

    So this tests the mechanism where it lives: a loop in its own thread with
    nothing else to do, one coroutine parked on `q.get()`, and `notify`
    called from outside. `asyncio.Queue.put_nowait` enqueues fine from a
    foreign thread; what it cannot do is wake the selector, so the parked
    getter resumes only when the loop next wakes for some other reason ---
    never, when there is no other reason.

    Measured on 2026-10-17 before the fix: a bridge pumping 202 samples into
    two registered subscriber queues delivered 0 live events to either.
    """
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    arrived = threading.Event()
    app: dict = {"subs": {}, "loop": None}
    topic = "t"

    async def parked():
        q: asyncio.Queue = asyncio.Queue()
        app["subs"].setdefault(topic, set()).add(q)
        app["loop"] = asyncio.get_running_loop()
        ready.set()
        await q.get()
        arrived.set()

    def spin():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.wait_for(parked(), timeout=20))

    th = threading.Thread(target=spin, daemon=True)
    th.start()
    if not ready.wait(5):
        return ("notify wakes a parked subscriber from a foreign thread",
                False, "the probe's own loop never started")
    time.sleep(0.3)                      # let the loop settle into idle
    gw_http.notify(app, topic, {"event": "sample"})
    woke = arrived.wait(THREAD_DEADLINE)
    loop.call_soon_threadsafe(loop.stop)
    return ("notify wakes a parked subscriber from a foreign thread, with "
            "that thread's loop idle",
            woke,
            f"woke within {THREAD_DEADLINE:.1f}s: {woke}")


async def _run() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    st = harness.state(harness.FOUNTAIN)
    topic = "spatialdds/ut-austin-south-mall/frames/geo_anchor/v1"
    path = "/ut-austin-south-mall/frames/geo_anchor/v1"
    res = st.resources[path]

    # The embedder's shape: its own backend, so no write surface at all.
    async with harness.Gateway(st, publish=False, live=True) as gw:
        async with aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {gw.token}"}) as s:
            ws_url = gw.base.replace("http", "ws") + path
            ws = await s.ws_connect(ws_url)
            drained = await _drain_quiet(ws)
            checks.append((
                "live_subscriptions keeps a subscription open with the write "
                "surface off",
                not ws.closed,
                f"drained {drained} queued event(s), socket open={not ws.closed}"))

            novel = dict(next(iter(st.latched[topic].values())).data)
            novel["anchor_id"] = "anchor:gate:injected"

            # --- the negative, first and deliberately
            publish.apply_sample(st, topic, res, "anchor:gate:injected", novel)
            got = await _one_event(ws, QUIET)
            checks.append((
                "apply_sample alone reaches nobody already on the socket",
                got is None,
                f"after {QUIET:.0f}s: {got if got is None else got.get('key')}"))

            # ...and yet it reached everything that is not a live socket
            async with s.get(gw.base + path) as r:
                ids = {a["anchor_id"] for a in await r.json()}
            checks.append((
                "...while reaching the collection GET",
                "anchor:gate:injected" in ids,
                f"{len(ids)} instance(s) served, injected present: "
                f"{'anchor:gate:injected' in ids}"))

            ws2 = await s.ws_connect(ws_url)
            keys = []
            while True:
                ev = await _one_event(ws2, PATIENCE)
                if ev is None or ev.get("event") == "end_of_replay":
                    break
                if ev.get("key"):
                    keys.append(ev["key"])
            checks.append((
                "...and a new subscriber's replay",
                "anchor:gate:injected" in keys,
                f"{len(keys)} replayed, injected present: "
                f"{'anchor:gate:injected' in keys}"))
            await ws2.close()

            # --- the positive: notify wakes the open one
            gw_http.notify(gw.app, topic,
                           {"event": "sample", "topic": topic,
                            "key": "anchor:gate:injected", "data": novel})
            got = await _one_event(ws, PATIENCE)
            checks.append((
                "notify reaches the subscriber apply_sample could not",
                got is not None and got.get("key") == "anchor:gate:injected",
                f"received {got and got.get('key')!r}"))

            # and a dispose travels the same way
            publish.apply_dispose(st, topic, "anchor:gate:injected")
            gw_http.notify(gw.app, topic,
                           {"event": "dispose", "topic": topic,
                            "key": "anchor:gate:injected", "reason": "gate"})
            got = await _one_event(ws, PATIENCE)
            checks.append((
                "a dispose travels the same seam",
                got is not None and got.get("event") == "dispose",
                f"received {got and got.get('event')!r}"))
            await ws.close()

            # --- the write surface really is off
            async with s.put(gw.base + path + "/anchor:gate:injected",
                             json=novel) as r:
                put_code = r.status
            checks.append((
                "...and the write surface stayed off while all that happened",
                put_code == 405,
                f"PUT -> {put_code}"))

    # --- the old caller's behaviour is unchanged
    async with harness.Gateway(harness.state(harness.FOUNTAIN),
                               publish=True) as gw:
        async with aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {gw.token}"}) as s:
            ws = await s.ws_connect(gw.base.replace("http", "ws") + path)
            await _drain_quiet(ws)
            checks.append((
                "publish_enabled alone still keeps sockets open, as before",
                not ws.closed,
                f"socket open={not ws.closed} with live_subscriptions unset"))
            await ws.close()

    # --- and with neither, the recorded stream still ends the way it did
    async with harness.Gateway(harness.state(harness.FOUNTAIN)) as gw:
        async with aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {gw.token}"}) as s:
            ws = await s.ws_connect(gw.base.replace("http", "ws") + path)
            saw_liveliness = False
            while True:
                ev = await _one_event(ws, PATIENCE)
                if ev is None:
                    break
                if ev.get("event") == "liveliness":
                    saw_liveliness = True
            checks.append((
                "with neither switch, a finished recording still says so and "
                "closes",
                saw_liveliness,
                f"liveliness events seen: {saw_liveliness}"))
    return checks


def main() -> int:
    checks = asyncio.run(_run())
    checks.append(_threaded_notify_check())
    ok = all(c[1] for c in checks)
    return harness.report(
        "a live backend: apply_sample, notify, and the flag that is now two",
        checks,
        "Both halves of the live-backend contract hold, and the negative that "
        "makes them two halves holds with them: apply_sample alone reaches a "
        "new subscriber's replay and the collection GET and never an open "
        "socket, which is what notify is for. The write surface and the "
        "socket lifetime are separate switches, and a caller that only ever "
        "passed publish_enabled is unaffected."
        if ok else
        "The live-backend contract does not hold as documented.")


if __name__ == "__main__":
    sys.exit(main())
