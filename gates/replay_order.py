#!/usr/bin/env python3
"""Latched replay, exactly one end_of_replay, then live. With a checked count.

The one thing the web binding adds that a bus does not have. On DDS, "you now
hold the whole latched set" is implicit in the QoS; over a WebSocket a client
cannot tell a slow replay from a quiet topic, so the binding makes the boundary
a message and makes it countable.

Three claims that fail separately: every event before the marker is a sample;
there is exactly one marker, not at least one; and `replayed` equals both the
samples actually sent and the recording's own count of instances in force when
the window opened.

Usage:  gates/replay_order.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import websockets                                              # noqa: E402

from harness import Gateway, TOKEN, report, state              # noqa: E402
from spatialdds_web import gateway                             # noqa: E402

MARKER = "end_of_replay"


async def collect(url: str, token: str | None, limit: int = 4000) -> list[dict]:
    hdr = {"Authorization": f"Bearer {token}"} if token else {}
    out: list[dict] = []
    async with websockets.connect(url, additional_headers=hdr,
                                  max_size=8 << 20) as c:
        try:
            while len(out) < limit:
                out.append(json.loads(await asyncio.wait_for(c.recv(), 20)))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
    return out


async def run() -> int:
    st = state()
    gw = await Gateway(st).start()
    checks: list[tuple[str, bool, str]] = []
    subscribed = 0
    nonzero = 0

    for path in sorted(st.resources):
        res = st.resources[path]
        if not res.latched:
            continue
        url = gw.base.replace("http://", "ws://") + path
        evs = await collect(url, TOKEN if res.access == gateway.OPERATOR
                            else None)
        subscribed += 1
        label = f"{res.scene[:8]}/{res.type_seg}"
        marks = [i for i, e in enumerate(evs) if e.get("event") == MARKER]
        checks.append((f"{label}: exactly one {MARKER}", len(marks) == 1,
                       f"found {len(marks)}"))
        if len(marks) != 1:
            continue
        at = marks[0]
        before = evs[:at]
        want = len(st.latched_initial.get(res.topic, {}))
        if want:
            nonzero += 1
        checks.append((f"{label}: every event before the marker is a sample",
                       all(e.get("event") == "sample" for e in before), ""))
        checks.append((f"{label}: replayed={evs[at].get('replayed')} equals "
                       f"the {len(before)} sample(s) before it",
                       evs[at].get("replayed") == len(before), ""))
        checks.append((f"{label}: replayed equals the {want} instance(s) in "
                       f"force when the window opened",
                       evs[at].get("replayed") == want, ""))
        checks.append((f"{label}: the marker carries exactly event, topic and "
                       f"replayed",
                       sorted(evs[at]) == ["event", "replayed", "topic"],
                       f"keys {sorted(evs[at])}"))
        checks.append((f"{label}: the replayed keys are those instances",
                       sorted(e.get("key") for e in before)
                       == sorted(st.latched_initial.get(res.topic, {})), ""))

    await gw.stop()
    checks.append((f"{subscribed} latched resource(s) were subscribed",
                   subscribed > 0, "nothing was subscribed"))
    checks.append((f"{nonzero} of them replay a non-empty set, so the count "
                   f"checks are not all trivially zero",
                   nonzero > 0, "every replay set was empty"))
    return report("replay ordering", checks,
                  "latched replay, one marker, then live")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
