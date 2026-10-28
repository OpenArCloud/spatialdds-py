#!/usr/bin/env python3
"""A deployment's own type, recorded and served: described, latched, and scoped as asked.

Finding 72 (S19, S20), from SpatialDDS-demo's OWM 0.2 prototype, the first
extension served over the binding and recorded. Until 0.7.4 the gateway
served such a type with no schema, though N.2 says "An endpoint MUST publish
a schema for every type it serves", and `serve --mcap` served its lane
volatile and operator-scoped whatever its writer opened.

* **The schema writer is the specification's generator, held to it.** From
  the pin's own model, `webjson.schema_from_model` reproduces every vendored
  schema exactly: those were written by the specification's
  `web-binding/gen.py`, from `idlc`'s output, so a schema it writes for an
  extension is the one that generator would have written. Its injection arm:
  with the model's integer constants cleared, the types whose sequence
  bounds are named (`SZ_SMALL`) must stop agreeing, so the comparison is
  seen to reach a bound.
* **S19, served.** A latched extension lane and a volatile one, written by
  the native layer with its recording tap on. The schema index lists both
  types; each schema answers with an `ETag` equal to the digest the index
  advertises; every sample the gateway serves validates against its type's
  schema.
* **S20, classified.** The tap writes each channel's durability, so the
  latched lane is served latched (its instance is a GET) and the volatile
  one is not. Both are operator-scoped, until `public=` names the latched
  type: then an uncredentialed GET of it is 200, and of the other still 401.

Usage:  gates/extension_served.py
"""
from __future__ import annotations

import asyncio
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402
import jsonschema                                              # noqa: E402

from harness import Gateway, report                            # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.server import gateway, pin, webjson            # noqa: E402

SCENE, SVC = "gate-extension", "svc:gate:extension"
FRAME = sd.frame("map/gate", convention="ENU", meters_per_unit=1.0)
LATCHED = sd.dds.Profile("GATE_EXT", reliability="RELIABLE",
                         durability="TRANSIENT_LOCAL", history="KEEP_LAST",
                         depth=1)
FAST = sd.dds.Profile("GATE_FAST", reliability="BEST_EFFORT",
                      durability="VOLATILE", history="KEEP_LAST", depth=1)
READING, NOTE = "gate_ext.Reading", "gate_ext.Note"
WK = "/.well-known/spatialdds"


def generator(checks: list) -> None:
    vend = webjson.schemas()
    model, _ = pin.model_from_records(
        [pin.schema_record(f.replace(".", "::")) for f in vend])
    differ = [f for f in vend if webjson.schema_from_model(model, f) != vend[f]]
    checks.append((f"from the pin's model, the schema writer reproduces every "
                   f"vendored schema exactly ({len(vend)})",
                   len(vend) > 100 and not differ, f"differ: {differ}"))
    named = 0
    model.constants.clear()
    for f in vend:
        try:
            webjson.schema_from_model(model, f)
        except KeyError:
            named += 1
    checks.append(("injection: with the model's constants cleared, the types "
                   "whose bounds are named no longer write (the comparison "
                   "reaches a bound)", named > 0, f"{named}"))


def record(path: Path, domain: int) -> None:
    from _ext_type import EXT_IDL, Note, Reading
    sd.dds.extend("gate.reading", Reading, EXT_IDL)
    sd.dds.extend("gate.note", Note, EXT_IDL)
    with sd.participant(SCENE, domain=domain, service_id=SVC) as p:
        p.record(path)
        w = p.writer("gate.reading", stream="ext", qos_profile=LATCHED,
                     on_exit="keep")
        n = p.writer("gate.note", stream="ext", qos_profile=FAST)
        for i in range(3):
            w.write({"reading_id": f"r{i}", "label": "gate", "value": i + 0.5,
                     "frame_ref": FRAME})
            n.write({"note_id": f"n{i}", "text": f"note {i}"})
        time.sleep(0.2)


async def served(checks: list, rec: Path) -> None:
    lanes = {READING: f"/{SCENE}/ext/gate.reading/v1",
             NOTE: f"/{SCENE}/ext/gate.note/v1"}
    st = gateway.build_state(rec, None)
    got = {f: (st.resources[p].latched, st.resources[p].access)
           for f, p in lanes.items() if p in st.resources}
    checks.append(("S20: the tap's channel durability reaches the loader: the "
                   "latched lane is latched, the volatile one is not, and "
                   "both are operator-scoped by default",
                   got == {READING: (True, gateway.OPERATOR),
                           NOTE: (False, gateway.OPERATOR)}, f"{got}"))
    async with Gateway(st) as gw, aiohttp.ClientSession() as s:
        auth = {"Authorization": f"Bearer {gw.token}"}
        async with s.get(gw.base + f"{WK}/schemas") as r:
            index = {e["type"]: e for e in await r.json()}
        checks.append(("S19: the schema index lists both extension types",
                       READING in index and NOTE in index,
                       f"{sorted(t for t in index if t.startswith('gate'))}"))
        schemas = {}
        for fqn in (READING, NOTE):
            if fqn not in index:
                continue
            async with s.get(gw.connect(index[fqn]["url"])) as r:
                schemas[fqn] = (r.status, r.headers.get("ETag", ""),
                                await r.json())
        tags_ok = all(st_ == 200 and etag.strip('"') == index[f]["digest"]
                      for f, (st_, etag, _d) in schemas.items())
        checks.append(("S19: each answers 200, with an ETag equal to the "
                       "digest the index advertises",
                       len(schemas) == 2 and tags_ok,
                       f"{[(f, v[0], v[1], index[f]['digest']) for f, v in schemas.items()]}"))
        # Everything served: latched instances (an instance first seen at
        # its topic's earliest log time is served as latched state, not as
        # a live event) and every live sample.
        bad, keys, n = [], set(), 0
        for fqn, path in lanes.items():
            topic = st.resources[path].topic
            docs = [(i.key, i.data) for i in st.latched.get(topic, {}).values()]
            docs += [(e["key"], e["data"]) for e in st.live.get(topic, [])
                     if e["event"] == "sample"]
            for key, data in docs:
                n += 1
                keys.add(key)
                try:
                    jsonschema.validate(data, schemas[fqn][2])
                except Exception as e:                    # noqa: BLE001
                    bad.append(f"{fqn} {key}: {e}")
        checks.append((f"S19: every document served, latched and live, "
                       f"validates against its type's published schema ({n})",
                       keys == {"r0", "r1", "r2", "n0", "n1", "n2"}
                       and not bad, f"{sorted(keys)} {bad[:3]}"))
        async with s.get(gw.base + lanes[READING], headers=auth) as r:
            body = await r.json() if r.status == 200 else None
        checks.append(("S20: the latched lane's collection holds its three "
                       "instances", r.status == 200 and body is not None
                       and sorted(d["reading_id"] for d in body)
                       == ["r0", "r1", "r2"], f"{r.status} {body}"))
        async with s.get(gw.base + lanes[READING]) as r:
            checks.append(("S20: uncredentialed, it is 401 while nobody has "
                           "said it is public", r.status == 401, f"{r.status}"))

    pub = gateway.build_state(rec, None, public=[READING])
    async with Gateway(pub) as gw, aiohttp.ClientSession() as s:
        status = {}
        for fqn, path in lanes.items():
            async with s.get(gw.base + path) as r:
                status[fqn] = r.status
        checks.append(("S20: with public= naming the latched type, an "
                       "uncredentialed GET of it is 200, and of the other "
                       "still 401", status == {READING: 200, NOTE: 401},
                       f"{status}"))


def run() -> int:
    checks: list = []
    generator(checks)
    with tempfile.TemporaryDirectory() as tmp:
        rec = Path(tmp) / "ext.mcap"
        try:
            record(rec, random.randint(150, 229))
            asyncio.run(served(checks, rec))
        except Exception as e:                            # noqa: BLE001
            checks.append(("the served arm ran to its end", False,
                           f"{type(e).__name__}: {e}"))
    return report("an extension, served", checks,
                  "a deployment's own type is described, latched and scoped "
                  "as its writer and its deployment said (finding 72)")


if __name__ == "__main__":
    sys.exit(run())
