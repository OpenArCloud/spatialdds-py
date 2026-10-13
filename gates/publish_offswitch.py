#!/usr/bin/env python3
"""With the flag off, this is the endpoint it was before the write surface.

The one that matters. A feature nobody asked for must not change what everyone
already relies on, so every read is compared byte for byte between a gateway
with publishing enabled and one without, and the write verbs are checked to be
refused rather than merely unused.

The comparison is two gateways over the same state, not a recorded baseline:
a baseline file would drift, and what needs proving is that the flag is the
only difference.

Usage:  gates/publish_offswitch.py
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets                                              # noqa: E402

from publish_common import (aiohttp, authored, authority_base, delete,   # noqa: E402
                            get, put, seeded)
from harness import Gateway, report                            # noqa: E402

# Headers whose value legitimately varies between two processes.
VARIES = {"Date", "Server", "Content-Length", "Transfer-Encoding"}


async def snapshot(gw, paths: list[str]) -> dict:
    out = {}
    async with aiohttp.ClientSession() as s:
        for p in paths:
            st_, h, raw = await get(s, gw.base, p)
            hdrs = {k: v for k, v in h.items() if k not in VARIES}
            # The advertised base appears in manifests; both gateways here
            # advertise the same one, so no rewriting is needed.
            out[p] = (st_, tuple(sorted(hdrs.items())), raw)
    return out


async def main() -> int:
    doc = authored()
    base = authority_base(doc)
    spec = doc["service"]
    checks: list[tuple[str, bool, str]] = []

    off = await Gateway(seeded(doc), advertise=base, publish=False).start()
    paths = ["/.well-known/spatialdds/search", "/.well-known/spatialdds/schemas",
             "/.well-known/spatialdds/resolver", "/", "/no/such/topic/v1",
             spec["path"], f"{spec['path']}/fountain-anchors",
             doc["content"]["path"]]
    snap_off = await snapshot(off, paths)

    async with aiohttp.ClientSession() as s:
        st_, h, raw = await put(s, off.base, f"{spec['path']}/{spec['key']}",
                                spec["record"])
        d = json.loads(raw) if raw else {}
        checks.append(("PUT is refused 405 with the flag off", st_ == 405,
                       f"got {st_}"))
        checks.append(("and the refusal says the surface is off, as a problem "
                       "document",
                       h.get("Content-Type", "").startswith(
                           "application/problem+json")
                       and d.get("type", "").endswith("publish-disabled"),
                       f"{h.get('Content-Type')!r} {d.get('type')!r}"))
        std, _, _ = await delete(s, off.base, f"{spec['path']}/fountain-anchors")
        checks.append(("DELETE likewise, and the instance survives it",
                       std == 405, f"got {std}"))
        st2, _, raw2 = await get(s, off.base,
                                 f"{spec['path']}/fountain-anchors")
        checks.append(("the instance a DELETE was attempted on is untouched",
                       st2 == 200 and raw2 == snap_off[
                           f"{spec['path']}/fountain-anchors"][2],
                       f"got {st2}"))

    # The subscription still terminates with the flag off, which is the
    # behaviour every existing WS gate depends on.
    ws_url = off.base.replace("http://", "ws://") + spec["path"]
    closed = False
    try:
        async with websockets.connect(ws_url) as ws:
            while True:
                await asyncio.wait_for(ws.recv(), 10)
    except websockets.ConnectionClosed:
        closed = True
    except asyncio.TimeoutError:
        closed = False
    checks.append(("a subscription still ends by itself with the flag off",
                   closed, "the socket stayed open"))
    await off.stop()

    on = await Gateway(seeded(doc), advertise=base, publish=True).start()
    snap_on = await snapshot(on, paths)
    await on.stop()

    for p in paths:
        so, sn = snap_off[p], snap_on[p]
        checks.append((f"read of {p} is byte-identical either way",
                       so == sn,
                       f"status {so[0]}/{sn[0]}; "
                       f"body {len(so[2])}/{len(sn[2])} bytes; "
                       f"header diff "
                       f"{sorted(set(so[1]) ^ set(sn[1]))[:2]}"))

    return report("publish off switch", checks,
                  "the flag is the only difference, and reads are not it")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
