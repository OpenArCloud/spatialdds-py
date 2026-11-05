#!/usr/bin/env python3
"""A refused write creates no lane, and every writable type has its schema.

Finding 73: a `PUT` refused with 400 and a `DELETE` answered 404, on a lane
that did not exist, registered the lane, and search advertised it until the
next restart. Finding 79: a deployment's own publishable type had no schema in
the index until a lane of it existed, though every body sent to it was held to
that schema. The two interacted (the refusal opened the lane, and the open
lane listed the schema), so they are fixed and gated together.

On a registry with a journal, a token table and a deployment's claim type,
nothing written yet:

  * the schemas index lists the claim type, and its schema answers 200;
  * each of finding 73's refused rows, on a registry of its own where the
    lane does not exist, answers its documented refusal and leaves search
    (body and ETag), both lanes' `GET` and the schemas index exactly as they
    were, then and after a restart on that registry's journal;
  * an accepted first write still registers its lane, in search and by `GET`,
    and a restart on its journal serves it.

Usage:  gates/phantom_lanes.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402

from harness import Gateway, blank, report                     # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.server import gateway, tenancy                 # noqa: E402

from _claim_type import CLAIM_IDL, Claim                       # noqa: E402

SCENE = "phantom-world"
ENT = f"/{SCENE}/world/entity/v1"
CLM = f"/{SCENE}/world/gate.claim/v1"
SEARCH = "/.well-known/spatialdds/search"
SCHEMAS = "/.well-known/spatialdds/schemas"
FRAME = sd.frame(f"map/{SCENE}", convention="ENU", meters_per_unit=1.0)
TOKEN = "tok-phantom-lanes"
EXT = sd.dds.extend("gate.claim", Claim, CLAIM_IDL)
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def entity(eid: str, basis: str) -> dict:
    return sd.entity(eid, frame_ref=FRAME, source_id="svc:phantom:world",
                     position=[0.0, 0.0, 0.0], basis=basis)


def claim(cid: str) -> dict:
    return {"id": cid, "subject_ref": "ent:a",
            "predicate": "https://example.org/predicate/near",
            "object_ref": "ent:b", "basis": "DECLARED", "asserter": "",
            "stamp": sd.stamp(), "state": "CREATED", "state_reason": ""}


def tokens() -> tenancy.TokenTable:
    return tenancy.TokenTable([(tenancy.digest(TOKEN), tenancy.Principal(
        "phantom", "example.org", frozenset({SCENE}), may_create_scenes=True))])


def fresh_state() -> gateway.State:
    st = blank()
    gateway.add_extension(st, EXT.type_name, EXT.fqn, EXT.record)
    return st


async def surface(s, gw, lanes: list[str]) -> dict:
    """What a reader can see: search with its ETag, each lane, the index."""
    out = {}
    async with s.get(gw.base + SEARCH) as r:
        out["search"] = (r.status, r.headers.get("ETag"), await r.read())
    for p in lanes:
        async with s.get(gw.base + p, headers=AUTH) as r:
            out[p] = r.status
    async with s.get(gw.base + SCHEMAS) as r:
        # By type and digest: each entry's URL carries this registry's port,
        # which a restart changes.
        out["schemas"] = sorted((e["type"], e["digest"]) for e in await r.json())
    return out


def listed(raw: bytes, path: str) -> bool:
    doc = json.loads(raw)
    return any(t["url"].endswith(path)
               for r in doc.get("results", []) for t in r["service"]["topics"])


async def refused_row(checks: list, d: Path, i: int, label: str,
                      method: str, path: str, body, status: int,
                      slug: str) -> None:
    """One refused row, on a registry of its own where no lane exists yet.

    A registry per row, because a row whose refusal opened a lane would
    otherwise leave the next row's lane existing, and that row would pass
    without testing anything.
    """
    jr = d / f"row{i}.mcap"
    lanes = [ENT, CLM]
    async with Gateway(fresh_state(), publish=True, journal=jr,
                       tokens=tokens()) as gw, aiohttp.ClientSession() as s:
        before = await surface(s, gw, lanes)
        kw = {"json": body} if body is not None else {}
        async with s.request(method, gw.base + path, headers=AUTH, **kw) as r:
            got = r.status
            doc = await r.json(content_type=None)
        after = await surface(s, gw, lanes)
    async with Gateway(fresh_state(), publish=True, journal=jr,
                       tokens=tokens()) as gw, aiohttp.ClientSession() as s:
        restarted = await surface(s, gw, lanes)
    kind = str(doc.get("type", "")).rsplit(":", 1)[-1]
    lane = path.rsplit("/", 1)[0]
    checks.append((f"{label}, on a lane that does not exist: {status} {slug}, "
                   f"and search (body and ETag), both lanes' GET and the "
                   f"schemas index are as they were, then and after a restart "
                   f"(finding 73)",
                   (got, kind) == (status, slug) and before[lane] == 404
                   and before == after == restarted,
                   f"answered {got} {kind}; lane GET {before[lane]} -> "
                   f"{after[lane]} -> {restarted[lane]} (restarted); search "
                   f"ETag {before['search'][1]} -> {after['search'][1]} -> "
                   f"{restarted['search'][1]}; index "
                   f"{'same' if before['schemas'] == after['schemas'] == restarted['schemas'] else 'changed'}"))


async def main_async(checks: list) -> None:
    bad_entity = {k: v for k, v in entity("ent:bad", "DECLARED").items()
                  if k != "layer"}
    bad_claim = {k: v for k, v in claim("c-bad").items() if k != "predicate"}
    # Finding 73's refused rows.
    rows = [
        ("DELETE a missing entity", "DELETE", f"{ENT}/ent:none", None,
         404, "no-such-instance"),
        ("PUT a schema-invalid entity", "PUT", f"{ENT}/ent:bad", bad_entity,
         400, "schema-invalid"),
        ("PUT an OBSERVED entity with no ?refresh=", "PUT", f"{ENT}/ent:obs",
         entity("ent:obs", "OBSERVED"), 400, "refresh-required"),
        ("PUT a schema-invalid claim", "PUT", f"{CLM}/c-bad", bad_claim,
         400, "schema-invalid"),
        ("DELETE a missing claim", "DELETE", f"{CLM}/c-none", None,
         404, "no-such-instance"),
    ]
    lanes = [ENT, CLM]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        async with Gateway(fresh_state(), publish=True, tokens=tokens()) as gw, \
                aiohttp.ClientSession() as s:
            async with s.get(gw.base + SCHEMAS) as r:
                index = await r.json()
            url = next((e["url"] for e in index if e["type"] == EXT.fqn), None)
            st = None
            if url is not None:
                async with s.get(gw.connect(url)) as r:
                    st = r.status
        checks.append((f"before any write, the schemas index lists the "
                       f"deployment's type {EXT.fqn}, and its schema answers "
                       f"200 (finding 79)",
                       url is not None and st == 200,
                       f"listed {url is not None}, schema {st}"))

        for i, row in enumerate(rows):
            await refused_row(checks, d, i, *row)

        jr = d / "accepted.mcap"
        async with Gateway(fresh_state(), publish=True, journal=jr,
                           tokens=tokens()) as gw, aiohttp.ClientSession() as s:
            async with s.put(gw.base + f"{ENT}/ent:a", headers=AUTH,
                             json=entity("ent:a", "DECLARED")) as r:
                put = r.status
            now = await surface(s, gw, lanes)
        checks.append(("an accepted first write still registers its lane: "
                       "201, listed in search, and its GET answers 200",
                       put == 201 and listed(now["search"][2], ENT)
                       and now[ENT] == 200,
                       f"PUT {put}; listed {listed(now['search'][2], ENT)}; "
                       f"GET {now[ENT]}"))
        async with Gateway(fresh_state(), publish=True, journal=jr,
                           tokens=tokens()) as gw, aiohttp.ClientSession() as s:
            again = await surface(s, gw, lanes)
            ids = []
            if again[ENT] == 200:
                async with s.get(gw.base + ENT, headers=AUTH) as r:
                    ids = [e["entity_id"] for e in await r.json()]
        checks.append(("…and a registry restarted on its journal serves that "
                       "lane, holding the accepted record",
                       listed(again["search"][2], ENT) and ids == ["ent:a"],
                       f"listed {listed(again['search'][2], ENT)}, holding {ids}"))


def main() -> int:
    checks: list = []
    asyncio.run(main_async(checks))
    return report("phantom lanes", checks,
                  "a refusal leaves the surface as it was, an accepted write "
                  "registers its lane, and every writable type has its schema")


if __name__ == "__main__":
    sys.exit(main())
