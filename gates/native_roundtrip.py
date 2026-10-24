#!/usr/bin/env python3
"""A native participant's session, recorded by its tap and served by the gateway.

The round trip through the published ecosystem, as the native layer's
acceptance asks. An SDK participant on its own domain publishes:

* an entity on `model_entity` (on_exit withdraw);
* poses on `model_pose`;
* a content item on the well-known `ContentAnnounce` lane, withdrawn mid-session
  with a reason. That type has no member to carry the reason, so the
  recording's metadata does;
* its announce.

It records the session with `p.record()` and stops cleanly. Then the gateway
serves the file (`gateway.build_state`, as `spatialdds-serve --mcap` does),
and this gate checks:

* every topic is served where N.3 puts it, including the four-segment
  discovery lanes in the well-known namespace;
* withdrawals are not in the latched set, and the stream carries each as its
  terminal sample then a dispose with the reason, for the entity (RETIRED, its
  own member) and for the content item (the recording's metadata);
* every recorded payload decodes, and its N.1 projection validates against the
  published schema. The counts are by type;
* the gateway serves exactly what the native layer's `to_json` says it wrote.

Usage:  gates/native_roundtrip.py
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402
import jsonschema                                              # noqa: E402
import websockets                                              # noqa: E402
from mcap.reader import make_reader                            # noqa: E402

from harness import Gateway, TOKEN, report                    # noqa: E402

import spatialdds as sd                                       # noqa: E402
from spatialdds.server import gateway, webjson, xcdr2          # noqa: E402

SCENE, SVC, ENT = "gate-roundtrip", "svc:gate:roundtrip", "ent:gate:duck"
WK = gateway.WELL_KNOWN_ROOT
REASON = "superseded by the gate's next item"


def session(path: Path, domain: int) -> dict:
    """Run the participant. Returns what it wrote, as the native layer's N.1."""
    wrote: dict = {"entity": [], "content": []}
    with sd.participant(SCENE, domain=domain, service_id=SVC,
                        authority="gate.example") as p:
        rec = p.record(path)
        frame = sd.frame("map/gate", convention="ENU", meters_per_unit=1.0)
        duck = p.writer("model_entity", qos_profile="MAP_META",
                        on_exit="withdraw")
        pose = p.writer("model_pose", qos_profile=sd.dds.Profile(
            "GATE_POSE", reliability="BEST_EFFORT", durability="VOLATILE"))
        content = p.writer("ContentAnnounce", on_exit="withdraw")
        p.announce("OTHER", coverage=sd.circle(30.28, -97.73, 50))
        wrote["entity"].append(sd.dds.to_json(duck.write(
            sd.entity(ENT, frame_ref=frame, source_id=SVC, position=(0, 0, 0)))))
        item = content.write(sd.content_announce(
            "content/gate-item", coverage=sd.circle(30.28, -97.73, 20),
            authority="gate.example", scene=SCENE, title="gate item"))
        wrote["content"].append(sd.dds.to_json(item))
        for i in range(5):
            pose.write(sd.model_pose(ENT, position=(i * 0.1, 0, 0),
                                     source_id=SVC))
        content.withdraw("content/gate-item", reason=REASON)
    wrote["counts"] = rec.counts
    return wrote


async def stream(url: str, token: str, n: int = 40) -> list:
    out = []
    async with websockets.connect(
            url, additional_headers={"Authorization": f"Bearer {token}"}) as c:
        try:
            while len(out) < n:
                out.append(json.loads(await asyncio.wait_for(c.recv(), 3)))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
    return out


async def run() -> int:
    checks: list = []
    with tempfile.TemporaryDirectory() as tmp:
        rec = Path(tmp) / "session.mcap"
        wrote = session(rec, random.randint(150, 229))
        checks.append((f"the tap recorded the session: {wrote['counts']}",
                       wrote["counts"]["samples"] > 0
                       and wrote["counts"]["disposes"] >= 3, ""))

        # Every recorded payload, decoded and validated with the gate's own
        # checker against the published schema.
        st = gateway.build_state(rec, None)
        dec, proj = xcdr2.Decoder(st.model), webjson.Projector(
            st.model, webjson.tables())
        schemas, counts, invalid = webjson.schemas(), {}, []
        with open(rec, "rb") as f:
            for sch, ch, msg in make_reader(f).iter_messages():
                fqn = sch.name.replace("::", ".")
                doc = proj.project(fqn, dec.decode(fqn, msg.data))
                try:
                    jsonschema.validate(doc, schemas[fqn])
                except Exception as e:
                    invalid.append(f"{fqn}: {e}")
                counts[fqn.rsplit(".", 1)[1]] = counts.get(
                    fqn.rsplit(".", 1)[1], 0) + 1
        total = sum(counts.values())
        checks.append((f"every one of the {total} recorded payloads validates "
                       f"against its published schema "
                       f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})",
                       not invalid and total == wrote["counts"]["samples"],
                       "; ".join(invalid[:2])))

        want = {f"/{SCENE}/world/entity/v1", f"/{SCENE}/world/model_pose/v1",
                f"{WK}/discovery/announce/v1", f"{WK}/discovery/depart/v1",
                f"{WK}/discovery/content/v1"}
        checks.append(("every topic is served where N.3 puts it, the "
                       "four-segment discovery lanes in the well-known "
                       "namespace", want <= set(st.resources),
                       f"{sorted(st.resources)}"))
        checks.append(("nothing in the recording went unnamed",
                       not [k for k in st.counts if k.startswith("unnamed:")],
                       f"{st.counts}"))

        async with Gateway(st) as gw, aiohttp.ClientSession() as s:
            hdr = {"Authorization": f"Bearer {TOKEN}"}
            latched = {}
            for path in sorted(want):
                async with s.get(gw.base + path, headers=hdr) as r:
                    latched[path] = (r.status, await r.json())
            checks.append(("the withdrawn entity, the withdrawn item and the "
                           "departed announce are not in their latched sets",
                           all(latched[p] == (200, []) for p in (
                               f"/{SCENE}/world/entity/v1",
                               f"{WK}/discovery/content/v1",
                               f"{WK}/discovery/announce/v1")),
                           f"{ {p: (c, len(d)) for p, (c, d) in latched.items()} }"))
            ws = gw.base.replace("http://", "ws://")
            ent = await stream(ws + f"/{SCENE}/world/entity/v1", TOKEN)
            kinds = [e["event"] for e in ent]
            i = kinds.index("dispose") if "dispose" in kinds else -1
            checks.append(("the entity's stream ends in its terminal sample "
                           "(RETIRED) then a dispose carrying its reason",
                           i > 0 and ent[i - 1]["event"] == "sample"
                           and ent[i - 1]["data"]["state"] == "RETIRED"
                           and ent[i]["reason"] == "the participant stopped",
                           f"{kinds}"))
            checks.append(("…and its first sample is exactly what the native "
                           "layer's to_json said it wrote",
                           ent and ent[0]["event"] == "sample"
                           and ent[0]["data"] == wrote["entity"][0],
                           ""))
            con = await stream(ws + f"{WK}/discovery/content/v1", TOKEN)
            kinds = [e["event"] for e in con]
            d = [e for e in con if e["event"] == "dispose"]
            checks.append(("the content item, a type with no member for a "
                           "reason, is withdrawn on the stream with the reason "
                           "its recording metadata carries",
                           d and d[0]["reason"] == REASON
                           and d[0]["key"] == "content/gate-item", f"{kinds}"))
            checks.append(("…and the item it served is exactly the native "
                           "to_json of what was written",
                           con and con[0]["event"] == "sample"
                           and con[0]["data"] == wrote["content"][0], ""))
            async with s.get(gw.base + f"{WK}/discovery/depart/v1",
                             headers=hdr) as r:
                checks.append(("the Depart lane is served, a stream rather "
                               "than a latched set", r.status == 200, ""))
    return report("native round trip", checks,
                  "a native session, recorded and served, is what the "
                  "participant wrote")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
