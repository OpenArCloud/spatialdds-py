#!/usr/bin/env python3
"""`--public` opens a type in one scene, not in every integrator's.

Finding 84: the access class was per type across a deployment, so an operator
who opened one integrator's world lanes to readers without a token opened
every integrator's. `--public SCENE/FQN` now opens that type in that scene
only; a bare `FQN` keeps its meaning, every scene.

On a registry with two principals, each with a scene of its own, one entity
written into each:

  * with `--public open-world/spatial.owm.Entity`, a reader with no token gets
    the open world's collection and instance (`public-cacheable`), and is
    refused the private world's (401); search says the same per lane;
  * a registry restarted on its journal keeps both classes;
  * with a bare `--public spatial.owm.Entity`, both worlds are public, as
    before;
  * `spatialdds-serve` refuses a rule that is neither shape, before it binds.

Usage:  gates/scene_access.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402

from harness import Gateway, report                            # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.server import gateway, tenancy                 # noqa: E402

FQN = "spatial.owm.Entity"
OPEN, PRIVATE = "open-world", "private-world"
SEARCH = "/.well-known/spatialdds/search"
TOKENS = {OPEN: "tok-scene-access-open", PRIVATE: "tok-scene-access-private"}


def lane(scene: str) -> str:
    return f"/{scene}/world/entity/v1"


def entity(scene: str) -> dict:
    return sd.entity(f"ent:{scene}", frame_ref=sd.frame(
        f"map/{scene}", convention="ENU", meters_per_unit=1.0),
        source_id=f"svc:{scene}:world", position=[0.0, 0.0, 0.0],
        basis="DECLARED")


def tokens() -> tenancy.TokenTable:
    return tenancy.TokenTable([
        (tenancy.digest(tok), tenancy.Principal(scene, "example.org",
                                                frozenset({scene}),
                                                may_create_scenes=True))
        for scene, tok in TOKENS.items()])


def state(public: list[str]) -> gateway.State:
    return gateway.build_state(None, None, public=public)


async def seen(s, gw) -> dict:
    """What a reader with no token gets, per scene: collection and instance
    status, the access header, and the class search advertises."""
    out = {}
    async with s.get(gw.base + SEARCH) as r:
        doc = await r.json()
    advertised = {t["url"].split("/", 3)[-1].split("/")[0]: t["access"]
                  for res in doc.get("results", [])
                  for t in res["service"]["topics"]
                  if t["url"].endswith("/world/entity/v1")}
    for scene in TOKENS:
        async with s.get(gw.base + lane(scene)) as r:
            coll = (r.status, r.headers.get("X-SpatialDDS-Access"))
        async with s.get(gw.base + f"{lane(scene)}/ent:{scene}") as r:
            inst = r.status
        out[scene] = (coll[0], inst, coll[1], advertised.get(scene))
    return out


async def write_both(s, gw) -> list[int]:
    got = []
    for scene, tok in TOKENS.items():
        async with s.put(gw.base + f"{lane(scene)}/ent:{scene}",
                         headers={"Authorization": f"Bearer {tok}"},
                         json=entity(scene)) as r:
            got.append(r.status)
    return got


PUBLIC = (200, 200, gateway.PUBLIC, gateway.PUBLIC)
SCOPED = (401, 401, None, gateway.OPERATOR)


async def main_async(checks: list) -> None:
    with tempfile.TemporaryDirectory() as td:
        jr = Path(td) / "journal.mcap"
        rule = [f"{OPEN}/{FQN}"]
        async with Gateway(state(rule), publish=True, journal=jr,
                           tokens=tokens()) as gw, aiohttp.ClientSession() as s:
            puts = await write_both(s, gw)
            now = await seen(s, gw)
        checks.append((f"--public {OPEN}/{FQN}: a reader with no token gets "
                       f"the open world's entities, public-cacheable in the "
                       f"header and in search, and is refused the private "
                       f"world's (401, operator-scoped) (finding 84)",
                       puts == [201, 201] and now[OPEN] == PUBLIC
                       and now[PRIVATE] == SCOPED,
                       f"PUTs {puts}; (collection, instance, header, search) "
                       f"{now}"))
        async with Gateway(state(rule), publish=True, journal=jr,
                           tokens=tokens()) as gw, aiohttp.ClientSession() as s:
            again = await seen(s, gw)
        checks.append(("…and a registry restarted on its journal keeps both "
                       "classes",
                       again[OPEN] == PUBLIC and again[PRIVATE] == SCOPED,
                       f"{again}"))

    async with Gateway(state([FQN]), publish=True, tokens=tokens()) as gw, \
            aiohttp.ClientSession() as s:
        puts = await write_both(s, gw)
        both = await seen(s, gw)
    checks.append((f"a bare --public {FQN} still opens the type in every "
                   f"scene, as before",
                   puts == [201, 201] and both[OPEN] == PUBLIC
                   and both[PRIVATE] == PUBLIC,
                   f"PUTs {puts}; {both}"))


def refused_rules(checks: list) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(ROOT / "lib" / "spatialdds")
    for rule in (f"/{FQN}", f"a/{OPEN}/{FQN}", f"{OPEN}/"):
        try:
            r = subprocess.run([sys.executable, "-m", "spatialdds.serve",
                                "--port", "0", "--public", rule],
                               env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60)
            code, err = r.returncode, r.stderr.strip()
        except subprocess.TimeoutExpired:
            code, err = None, "still running after 60 s: it did not refuse"
        checks.append((f"spatialdds-serve --public {rule!r} is refused before "
                       f"it binds, naming both shapes",
                       code not in (0, None) and "SCENE/FQN" in err,
                       f"exit {code}: {err[-300:]}"))


def main() -> int:
    checks: list = []
    asyncio.run(main_async(checks))
    refused_rules(checks)
    return report("scene access", checks,
                  "--public opens a type in the scene it names and no other")


if __name__ == "__main__":
    sys.exit(main())
