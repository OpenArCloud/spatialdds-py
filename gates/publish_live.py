#!/usr/bin/env python3
"""A publish reaches a subscriber already on the socket, and so does a dispose.

The recording stops being the only producer. A subscriber that connected before
anything was published must receive the new sample on the same envelope it
receives replayed ones -- `sample` then `dispose`, same fields, same topic --
because a client that had to distinguish them would be reading a different
protocol depending on where the data came from.

This is the one read-side behaviour `--publish` changes: with the flag on the
socket stays open after the recorded events instead of closing, which
`gates/publish_offswitch.py` checks is still the old behaviour with the flag
off.

Usage:  gates/publish_live.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys

import websockets                                              # noqa: E402

from publish_common import (aiohttp, authored, authority_base, delete,   # noqa: E402
                            put, seeded)
from harness import Gateway, report                            # noqa: E402


async def collect(ws, n: int, timeout: float = 10.0) -> list[dict]:
    out = []
    try:
        for _ in range(n):
            out.append(json.loads(
                await asyncio.wait_for(ws.recv(), timeout)))
    except (asyncio.TimeoutError, Exception):
        pass
    return out


async def main() -> int:
    doc = authored()
    base = authority_base(doc)
    spec = doc["service"]
    checks: list[tuple[str, bool, str]] = []
    gw = await Gateway(seeded(doc), advertise=base, publish=True).start()
    topic = f"spatialdds{spec['path']}"
    ws_url = gw.base.replace("http://", "ws://") + spec["path"]

    async with websockets.connect(ws_url) as ws:
        # Drain replay and the marker first: a subscriber is current only
        # after the marker, and that contract is unchanged.
        pre = []
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), 10))
            pre.append(ev)
            if ev["event"] == "end_of_replay":
                break
        checks.append((f"the subscriber reaches end_of_replay first "
                       f"({pre[-1]['replayed']} replayed)",
                       pre[-1]["event"] == "end_of_replay", f"{pre[-1]}"))

        # Nothing further should arrive on its own: the socket stays open
        # rather than being closed, and silence is not events.
        quiet = await collect(ws, 1, timeout=1.5)
        checks.append(("then the socket stays open and quiet, rather than "
                       "closing", not quiet, f"unexpected {quiet}"))

        async with aiohttp.ClientSession() as s:
            url = f"{spec['path']}/{spec['key']}"
            st_, _, _ = await put(s, gw.base, url, spec["record"])
            got = await collect(ws, 1)
            ev = got[0] if got else {}
            checks.append((f"a publish accepted while it is connected arrives "
                           f"as a sample", st_ == 201 and ev.get("event") == "sample",
                           f"PUT {st_}, event {ev.get('event')!r}"))
            checks.append(("on the right topic and key",
                           ev.get("topic") == topic
                           and ev.get("key") == spec["key"],
                           f"{ev.get('topic')!r} {ev.get('key')!r}"))
            checks.append(("carrying the served canonical record, identical "
                           "to the authored input",
                           ev.get("data") == spec["record"], "data differs"))
            checks.append(("with the same envelope fields a replayed sample "
                           "has",
                           sorted(ev) == sorted(
                               [k for k in pre[0]] if pre and
                               pre[0]["event"] == "sample"
                               else ["data", "event", "key", "topic"]),
                           f"{sorted(ev)}"))

            std, _, _ = await delete(s, gw.base, url + "?reason=withdrawn")
            got2 = await collect(ws, 1)
            dv = got2[0] if got2 else {}
            checks.append(("a dispose arrives as a dispose event",
                           std == 204 and dv.get("event") == "dispose",
                           f"DELETE {std}, event {dv.get('event')!r}"))
            checks.append((f"naming the key and a reason "
                           f"({dv.get('reason')!r})",
                           dv.get("key") == spec["key"]
                           and dv.get("reason") == "withdrawn",
                           f"{dv}"))

    # A subscriber that connects after the publish replays it as latched
    # state, because a published instance is latched state.
    async with aiohttp.ClientSession() as s:
        await put(s, gw.base, f"{spec['path']}/{spec['key']}", spec["record"])
    async with websockets.connect(ws_url) as ws2:
        seen = []
        while True:
            ev = json.loads(await asyncio.wait_for(ws2.recv(), 10))
            if ev["event"] == "end_of_replay":
                break
            seen.append(ev)
        keys = sorted(e["key"] for e in seen)
        checks.append((f"a later subscriber replays it as latched state "
                       f"({keys})", spec["key"] in keys, f"{keys}"))

    await gw.stop()
    return report("publish live", checks,
                  "a published record reaches subscribers on the same envelope")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
