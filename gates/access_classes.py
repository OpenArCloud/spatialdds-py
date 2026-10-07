#!/usr/bin/env python3
"""Configuration is public and cacheable; observation is gated and is not.

Tested in both directions, because a one-directional check is how access
control quietly stops working: that the token works proves the lock turns,
that its absence is refused proves there is a lock.

The structural invariant matters more than either: an operator-scoped resource
must never be served cacheable. A bearer-gated body with `max-age` on it sits
in a shared cache, where the next request without a token can be served it.

Usage:  gates/access_classes.py
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

import aiohttp                                                 # noqa: E402
import websockets                                              # noqa: E402

from harness import Gateway, TOKEN, report, state              # noqa: E402
from spatialdds_web import gateway                             # noqa: E402


async def run() -> int:
    st = state()
    gw = await Gateway(st).start()
    checks: list[tuple[str, bool, str]] = []
    npub = nop = 0

    async with aiohttp.ClientSession() as s:
        for path in sorted(st.resources):
            res = st.resources[path]
            label = f"{res.scene[:8]}/{res.type_seg}"
            async with s.get(gw.base + path) as r:
                anon, acc = r.status, r.headers.get("Cache-Control", "")
                wa = r.headers.get("WWW-Authenticate", "")
            async with s.get(gw.base + path,
                             headers={"Authorization": f"Bearer {TOKEN}"}) as r:
                tok, tcc = r.status, r.headers.get("Cache-Control", "")
            async with s.get(gw.base + path,
                             headers={"Authorization": "Bearer wrong"}) as r:
                wrong = r.status
            if res.access == gateway.PUBLIC:
                npub += 1
                checks.append((f"{label}: public, served without auth",
                               anon == 200, f"got {anon}"))
                checks.append((f"{label}: public, cacheable",
                               "max-age" in acc and "public" in acc, acc))
            else:
                nop += 1
                checks.append((f"{label}: refused without a token",
                               anon == 401, f"got {anon}"))
                checks.append((f"{label}: refused with a wrong token",
                               wrong == 401, f"got {wrong}"))
                checks.append((f"{label}: served with the right token",
                               tok == 200, f"got {tok}"))
                checks.append((f"{label}: the refusal says how to authenticate",
                               wa.startswith("Bearer"), wa))
                checks.append((f"{label}: never cacheable, either way",
                               "no-store" in acc and "no-store" in tcc,
                               f"{acc!r}/{tcc!r}"))

        contra = []
        for path, res in sorted(st.resources.items()):
            if res.access != gateway.OPERATOR:
                continue
            async with s.get(gw.base + path,
                             headers={"Authorization": f"Bearer {TOKEN}"}) as r:
                cc = r.headers.get("Cache-Control", "")
            if "max-age" in cc or "public" in cc:
                contra.append(f"{path} -> {cc}")
        checks.append(("no operator-scoped resource is served cacheable",
                       not contra, f"{contra[:2]}"))

    op = next((p for p, r in sorted(st.resources.items())
               if r.access == gateway.OPERATOR and r.latched), None)
    if op:
        url = gw.base.replace("http://", "ws://") + op
        refused = False
        try:
            async with websockets.connect(url) as c:
                await asyncio.wait_for(c.recv(), 5)
        except Exception:
            refused = True
        allowed = False
        try:
            async with websockets.connect(
                    url, additional_headers={
                        "Authorization": f"Bearer {TOKEN}"}) as c:
                json.loads(await asyncio.wait_for(c.recv(), 10))
                allowed = True
        except Exception:
            allowed = False
        checks.append(("a subscription to a gated topic is refused without a "
                       "token", refused, "the stream was served anonymously"))
        checks.append(("the same subscription succeeds with the token",
                       allowed, "the token was rejected"))

    await gw.stop()
    checks.append((f"both classes are present ({npub} public, {nop} gated)",
                   npub > 0 and nop > 0, "one class is unrepresented"))
    return report("access classes", checks,
                  "configuration is public, observation is gated")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
