#!/usr/bin/env python3
"""Two builds serve byte-identical latched resources, and ETags work.

A latched resource is a cacheable representation, so two things must hold or
caching is unsafe: the bytes must not depend on when the gateway started, and
the ETag must be a function of the content rather than of the process. Then the
conditional-GET contract in both directions, because a 304 that is never
returned is a cache that never works, and a 304 for changed content is a cache
that serves lies.

Usage:  gates/determinism.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

from harness import Gateway, TOKEN, report, state              # noqa: E402


async def fetch_all(gw: Gateway) -> dict[str, tuple[int, bytes, str]]:
    out: dict[str, tuple[int, bytes, str]] = {}
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    async with aiohttp.ClientSession() as s:
        for path in sorted(gw.state.resources):
            res = gw.state.resources[path]
            if not res.latched:
                continue
            paths = [path] + [f"{path}/{k}" for k in
                              sorted(gw.state.latched.get(res.topic, {}))]
            for p in paths:
                async with s.get(gw.base + p, headers=hdr) as r:
                    out[p] = (r.status, await r.read(),
                              r.headers.get("ETag", ""))
    return out


async def run() -> int:
    a = await Gateway(state()).start()
    first = await fetch_all(a)
    await a.stop()
    b = await Gateway(state()).start()
    second = await fetch_all(b)

    checks: list[tuple[str, bool, str]] = []
    checks.append((f"both builds serve the same {len(first)} latched path(s)",
                   set(first) == set(second),
                   f"{sorted(set(first) ^ set(second))[:3]}"))
    diff = [p for p in sorted(set(first) & set(second))
            if first[p][1] != second[p][1]]
    checks.append(("every latched body is byte-identical across two builds",
                   not diff, f"{len(diff)} differ: {diff[:2]}"))
    etag = [p for p in sorted(set(first) & set(second))
            if first[p][2] != second[p][2]]
    checks.append(("every ETag is identical, so it is a content digest",
                   not etag, f"{etag[:2]}"))
    nonempty = [p for p, v in first.items() if v[0] == 200 and v[1] != b"[]"]
    checks.append((f"the latched set is not trivially empty "
                   f"({len(nonempty)} of {len(first)} non-empty)",
                   bool(nonempty), "everything was empty"))

    probe = next((p for p, v in first.items() if v[0] == 200 and v[2]), None)
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    if probe is None:
        checks.append(("a resource with an ETag exists", False, "none"))
    else:
        async with aiohttp.ClientSession() as s:
            async with s.get(b.base + probe,
                             headers={**hdr,
                                      "If-None-Match": first[probe][2]}) as r:
                hit, body = r.status, await r.read()
            async with s.get(b.base + probe,
                             headers={**hdr,
                                      "If-None-Match": '"sha256:0"'}) as r:
                miss = r.status
        checks.append((f"a matching If-None-Match gets 304", hit == 304,
                       f"got {hit}"))
        checks.append(("a 304 carries no body", body == b"", f"{len(body)}"))
        checks.append(("a stale If-None-Match gets 200", miss == 200,
                       f"got {miss}"))
    await b.stop()
    return report("determinism", checks,
                  "the latched surface is deterministic and cacheable")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
