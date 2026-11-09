#!/usr/bin/env python3
"""The SDK says when an endpoint advertises an origin it was not read from.

Finding 83: the SDK reads an advertised topic URL's path on the base it
connected to, so an endpoint advertising `http://0.0.0.0:8089` read fine
through it and nowhere else, and nothing said so. Ruled 2026-11-09: warn,
once per endpoint, naming both origins.

Against the fountain registry and its manifests, advertising
`https://demo.spatialdds.org` (the manifests' authority, so resolver metadata
is served) while the client reads it on loopback:

  * `search()` warns `AdvertisedOriginWarning`, naming both origins, and
    still returns every service;
  * a second `search()` and a `resolve()` on the same endpoint warn nothing
    more: once per endpoint;
  * `resolve()` on a fresh endpoint warns too (resolver metadata);
  * a second endpoint to the same registry warns again: once per endpoint,
    not once per process;
  * the same registry advertising its own loopback origin warns nothing.

Usage:  gates/advertised_origin.py
"""
from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import FOUNTAIN, MANIFESTS, Gateway, report, state                            # noqa: E402
import spatialdds as sd                                        # noqa: E402

ELSEWHERE = "https://demo.spatialdds.org"


def caught(fn):
    """fn's result, and the AdvertisedOriginWarnings it raised."""
    with warnings.catch_warnings(record=True) as got:
        warnings.simplefilter("always")
        out = fn()
    return out, [str(w.message) for w in got
                 if issubclass(w.category, sd.AdvertisedOriginWarning)]


def resolve_quietly(ep):
    try:
        ep.resolve("spatialdds://demo.spatialdds.org/no-scene/anchor/none")
    except sd.SpatialDDSError:
        pass


async def run(checks: list) -> None:
    loop = asyncio.get_running_loop()
    off = lambda f: loop.run_in_executor(None, f)           # noqa: E731

    async with Gateway(state(FOUNTAIN, MANIFESTS), advertise=ELSEWHERE) \
            as gw:
        here = gw.base
        ep = sd.connect(here, lifted=False)
        hits, first = await off(lambda: caught(ep.search))
        checks.append(("search() on a registry advertising another origin "
                       "warns AdvertisedOriginWarning, naming both origins, "
                       "and still returns its services (finding 83)",
                       len(first) == 1 and here in first[0]
                       and ELSEWHERE in first[0] and len(hits) > 0,
                       f"{len(hits)} service(s); warnings {first}"))
        _, again = await off(lambda: caught(ep.search))
        _, res = await off(lambda: caught(lambda: resolve_quietly(ep)))
        checks.append(("…a second search() and a resolve() on the same "
                       "endpoint warn nothing more: once per endpoint",
                       again == [] and res == [],
                       f"search {again}; resolve {res}"))

        fresh = sd.connect(here, lifted=False)
        _, rw = await off(lambda: caught(lambda: resolve_quietly(fresh)))
        checks.append(("resolve() on a fresh endpoint warns from the "
                       "resolver metadata, naming both origins",
                       len(rw) == 1 and ELSEWHERE in rw[0]
                       and "resolver metadata" in rw[0], f"{rw}"))

        other = sd.connect(here, lifted=False)
        _, ow = await off(lambda: caught(other.search))
        checks.append(("a second endpoint to the same registry warns again: "
                       "once per endpoint, not once per process",
                       len(ow) == 1, f"{ow}"))

    async with Gateway(state(FOUNTAIN, MANIFESTS)) as gw:
        ep = sd.connect(gw.base, lifted=False)
        hits, none = await off(lambda: caught(ep.search))
        _, rnone = await off(lambda: caught(lambda: resolve_quietly(ep)))
    checks.append(("the same registry advertising the origin it is read "
                   "from warns nothing",
                   none == [] and rnone == [] and len(hits) > 0,
                   f"{len(hits)} service(s); warnings {none + rnone}"))


def main() -> int:
    checks: list = []
    asyncio.run(run(checks))
    return report("advertised origin", checks,
                  "the SDK names a misadvertised origin, once per endpoint")


if __name__ == "__main__":
    sys.exit(main())
