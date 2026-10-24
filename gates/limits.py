#!/usr/bin/env python3
"""Every stated limit, at its boundary: at the limit passes, one past refuses.

Each refusal must be an N.8 problem document naming the limit and its number,
because a bare 413 from a framework tells a client nothing it can act on. The
limits are set small here so the boundary is cheap to reach; the numbers are
the gate's own, never read back from the endpoint.

    gates/limits.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

from harness import FOUNTAIN, MANIFESTS, Gateway, report, state  # noqa: E402
from publish_common import resolve_path                       # noqa: E402

from spatialdds.server import gw_http, limits, tenancy         # noqa: E402

AUTH = "demo.spatialdds.org"
SCENE = "ut-austin-south-mall"
BODY = 4096


def manifest(name: str, size: int | None = None) -> bytes:
    """A valid manifest, padded so its encoded body is exactly `size` bytes."""
    doc = {"id": f"spatialdds://{AUTH}/{SCENE}/content/{name}",
           "profile": "spatial.manifest/1.8", "rtype": "content",
           "content": {"content_id": f"content/{name}", "title": name,
                       "summary": ""}}
    raw = json.dumps(doc).encode()
    if size is not None:
        doc["content"]["summary"] = "x" * (size - len(raw))
        raw = json.dumps(doc).encode()
        assert len(raw) == size, (len(raw), size)
    return raw


def is_problem(code: int, h, body: bytes, slug: str) -> bool:
    try:
        d = json.loads(body)
    except ValueError:
        return False
    return (h.get("Content-Type", "").startswith("application/problem+json")
            and d.get("status") == code and d.get("type", "").endswith(slug))


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    tok = tenancy.issue()
    st = state(FOUNTAIN, MANIFESTS)
    held = gw_http._scene_records(st, SCENE)
    table = tenancy.TokenTable([(tenancy.digest(tok), tenancy.Principal(
        "james", "openarcloud.org", frozenset({SCENE}), False,
        max_records=held + 1))])
    lim = limits.Limits(max_body=BODY, max_ws=2, rate_per_s=5.0, burst=5)
    gw = await Gateway(st, advertise=f"https://{AUTH}", publish=True,
                       tokens=table).start()
    gw.app["limits"] = lim
    # aiohttp's backstop for undeclared lengths is fixed when the app is
    # built; the declared-length check reads `limits` per request.
    gw.app._client_max_size = BODY
    auth = {"Authorization": f"Bearer {tok}",
            "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            b = gw.base

            # -- body: exactly at the limit, then one byte over --
            async with s.put(b + "/spatialdds/resolve", data=manifest("at", BODY),
                             headers=auth) as r:
                at = r.status
            async with s.put(b + "/spatialdds/resolve",
                             data=manifest("over", BODY + 1), headers=auth) as r:
                over, oh, ob = r.status, r.headers, await r.read()
            print(f"  body {BODY + 1}: {over} {ob.decode()}")
            checks.append((f"a {BODY}-byte body is accepted; {BODY + 1} is a "
                           f"413 problem document naming the limit",
                           at == 201 and over == 413
                           and is_problem(413, oh, ob, "body-too-large")
                           and json.loads(ob).get("limit_bytes") == BODY,
                           f"at={at} over={over}"))

            async def chunks():
                yield manifest("chunked", BODY + 1)
            async with s.put(b + "/spatialdds/resolve", data=chunks(),
                             headers=auth) as r:
                cc, ch, cb = r.status, r.headers, await r.read()
            checks.append(("an over-limit body that declares no length is the "
                           "same 413 problem document",
                           cc == 413 and is_problem(413, ch, cb,
                                                    "body-too-large"),
                           f"{cc} {cb[:120]!r}"))

            # -- quota: the scene now holds held + 1 (the `at` manifest) --
            async with s.put(b + "/spatialdds/resolve",
                             data=manifest("one-more"), headers=auth) as r:
                q1, qh, qb = r.status, r.headers, await r.read()
            async with s.put(b + "/spatialdds/resolve", data=manifest("at", 200),
                             headers=auth) as r:
                q2 = r.status
            print(f"  quota: {q1} {qb.decode()}")
            checks.append((f"max_records {held + 1}: the record that reached it "
                           f"was accepted, the next new one is a 403 "
                           f"quota-exceeded naming the limit",
                           q1 == 403 and is_problem(403, qh, qb,
                                                    "quota-exceeded")
                           and json.loads(qb).get("limit_records") == held + 1,
                           f"{q1}"))
            checks.append(("at the quota, replacing an existing record is "
                           "still allowed", q2 == 200, f"{q2}"))
            async with s.delete(b + resolve_path(
                    f"spatialdds://{AUTH}/{SCENE}/content/at"),
                    headers=auth) as r:
                d1 = r.status
            async with s.put(b + "/spatialdds/resolve",
                             data=manifest("one-more"), headers=auth) as r:
                q3 = r.status
            checks.append(("withdrawing one makes room for one", (d1, q3)
                           == (204, 201), f"{(d1, q3)}"))

            # -- WebSocket cap: two open, the third refused, then room --
            url = b + f"/{SCENE}/frames/geo_anchor/v1"
            w1 = await s.ws_connect(url)
            w2 = await s.ws_connect(url)
            async with s.get(url, headers={"Connection": "Upgrade",
                                           "Upgrade": "websocket",
                                           "Sec-WebSocket-Version": "13",
                                           "Sec-WebSocket-Key":
                                               "dGhlIHNhbXBsZSBub25jZQ=="}) as r:
                w3, wh, wb = r.status, r.headers, await r.read()
            print(f"  ws #3: {w3} {wb.decode()}")
            await w1.close()
            await asyncio.sleep(0.2)
            w4 = await s.ws_connect(url)
            ok4 = not w4.closed
            await w2.close()
            await w4.close()
            checks.append(("max_ws 2: the third subscription is a 503 problem "
                           "document naming the cap, and closing one makes "
                           "room", w3 == 503 and is_problem(
                               503, wh, wb, "too-many-subscriptions") and ok4,
                           f"{w3} reopened={ok4}"))

            # -- rate: 5/s burst 5, per forwarded client --
            path = b + "/.well-known/spatialdds/schemas"
            fwd = {"X-Forwarded-For": "198.51.100.7, 203.0.113.9"}
            codes = []
            for _ in range(6):
                async with s.get(path, headers=fwd) as r:
                    codes.append(r.status)
                    rh, rb = r.headers, await r.read()
            print(f"  rate #6: {codes[-1]} Retry-After={rh.get('Retry-After')} "
                  f"{rb.decode()}")
            checks.append(("burst 5: five requests from one forwarded client "
                           "pass, the sixth is a 429 problem document with "
                           "Retry-After",
                           codes[:5] == [200] * 5 and codes[5] == 429
                           and is_problem(429, rh, rb, "rate-limited")
                           and int(rh.get("Retry-After", "0")) >= 1,
                           f"{codes}"))
            async with s.get(path, headers={"X-Forwarded-For": "203.0.113.10"}
                             ) as r:
                other = r.status
            spoof = {"X-Forwarded-For": "203.0.113.9, 192.0.2.1"}
            async with s.get(path, headers=spoof) as r:
                spoofed = r.status
            checks.append(("another client has its own bucket, and the bucket "
                           "is the address the proxy appended, not one a "
                           "client prepends", other == 200 and spoofed == 200,
                           f"other={other} spoofed-prefix={spoofed}"))
            local = []
            for _ in range(20):
                async with s.get(path) as r:
                    local.append(r.status)
            checks.append(("the local operator (loopback, nothing forwarded) "
                           "is not rate-limited", set(local) == {200},
                           f"{sorted(set(local))}"))
    finally:
        await gw.stop()
    return report("limits", checks,
                  "every limit refuses one past its boundary with a problem "
                  "document naming it")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
