#!/usr/bin/env python3
"""Four-segment topics are served in the well-known namespace, and nothing is dropped.

§3.3.1 allows four-segment names for well-known topics, and the discovery lanes
are four-segment: `spatialdds/discovery/announce/v1`. The 1.8 stamp (N.3)
serves such a topic at `{base}/.well-known/spatialdds/<stream>/<type>/<version>`,
behaving exactly as a scene-scoped collection does. Until 0.7.0 this gateway
required five segments and dropped every four-segment topic without a word.
Measured by the spec's own conformance suite (`four_segment_probe.py`,
v1.8.2), an OpenVPS session served no resources at all.

This gate authors its own recording from the pin: one Announce, built by the
package's builder and encoded by the gateway's own writer, on a four-segment
topic and on a five-segment control. It then serves the recording and checks
both the same way. The absent arm is a well-known topic nobody published, which
must be a 404 problem document rather than an empty 200, because an empty 200
reads as a quiet topic.

Usage:  gates/short_topics.py
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
import websockets                                              # noqa: E402
from mcap.writer import Writer as McapWriter                   # noqa: E402

from harness import Gateway, report                            # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.server import gateway, pin, webjson, xcdr2     # noqa: E402

FQN = "spatial.disco.Announce"
FOUR = "spatialdds/discovery/announce/v1"
FIVE = "spatialdds/gate-scene/discovery/announce/v1"
WK = gateway.WELL_KNOWN_ROOT


def author(path: Path, topics: tuple = (FOUR, FIVE)) -> str:
    """A recording with one Announce on each topic. Returns its service_id."""
    model, idl = pin.wire_model()
    doc = sd.service_announce(
        "svc:gate:short-topic", "OTHER", coverage=sd.circle(30.28, -97.73, 50),
        authority="gate.example", scene="gate-scene")
    wire = webjson.Lifter(model, webjson.tables()).lift(FQN, doc)
    raw = xcdr2.Writer(model).encode(FQN, wire)
    name, data = idl[FQN]
    with open(path, "wb") as f:
        w = McapWriter(f)
        w.start(profile="spatialdds", library="gates/short_topics")
        sid = w.register_schema(name=name, encoding="omgidl", data=data)
        for i, topic in enumerate(topics):
            cid = w.register_channel(topic=topic, message_encoding="cdr",
                                     schema_id=sid)
            w.add_message(channel_id=cid, log_time=i + 1, publish_time=i + 1,
                          data=raw)
        w.finish()
    return doc["service_id"]


async def first_events(url: str, n: int = 2) -> list[dict]:
    out = []
    try:
        c = await websockets.connect(url)
    except websockets.InvalidStatus as e:
        # A refused upgrade is a result this gate reports, not a crash: it is
        # exactly what the five-segment-only gateway answered.
        return [{"event": f"refused {e.response.status_code}"}]
    async with c:
        try:
            while len(out) < n:
                out.append(json.loads(await asyncio.wait_for(c.recv(), 10)))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
    return out


async def run() -> int:
    checks: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        rec = Path(tmp) / "short.mcap"
        sid = author(rec)
        st = gateway.build_state(rec, None)
        four_path = f"{WK}/discovery/announce/v1"
        five_path = "/gate-scene/discovery/announce/v1"
        checks.append(("the loader indexes the four-segment topic at its "
                       "well-known path, not under a scene",
                       four_path in st.resources
                       and st.resources[four_path].scene == ""
                       and st.resources[four_path].topic == FOUR,
                       f"{sorted(st.resources)}"))
        checks.append(("and the five-segment control at its scene path",
                       five_path in st.resources, f"{sorted(st.resources)}"))
        checks.append(("nothing in the recording went unnamed",
                       not [k for k in st.counts if k.startswith("unnamed:")],
                       f"{st.counts}"))

        async with Gateway(st) as gw, aiohttp.ClientSession() as s:
            got = {}
            for label, path in (("four", four_path), ("five", five_path)):
                async with s.get(gw.base + path) as r:
                    got[label] = (r.status, await r.json(),
                                  r.headers.get("ETag", ""),
                                  r.headers.get("Cache-Control", ""))
            ok4, ok5 = got["four"], got["five"]
            checks.append((f"GET {four_path} is 200 with the one announce",
                           ok4[0] == 200 and len(ok4[1]) == 1
                           and ok4[1][0].get("service_id") == sid,
                           f"{ok4[0]} {str(ok4[1])[:80]}"))
            checks.append(("…byte-for-byte what the five-segment control serves, "
                           "with the same ETag and Cache-Control",
                           ok4[1] == ok5[1] and ok4[2] == ok5[2] and ok4[2]
                           and ok4[3] == ok5[3],
                           f"{ok4[2]} {ok5[2]} / {ok4[3]} {ok5[3]}"))
            async with s.get(gw.base + four_path,
                             headers={"If-None-Match": ok4[2]}) as r:
                checks.append(("a revalidation with that ETag is 304",
                               r.status == 304, f"{r.status}"))
            key = sid.replace(":", "%3A")
            async with s.get(gw.base + f"{four_path}/{key}") as r:
                body = await r.json()
                checks.append(("one instance is addressed below the "
                               "well-known collection",
                               r.status == 200
                               and body.get("service_id") == sid,
                               f"{r.status}"))
            ws = gw.base.replace("http://", "ws://")
            ev = await first_events(ws + four_path)
            checks.append(("upgrading the same path is the N.5 stream: the "
                           "sample, then end_of_replay",
                           [e.get("event") for e in ev]
                           == ["sample", "end_of_replay"],
                           f"{[e.get('event') for e in ev]}"))
            async with s.put(gw.base + f"{four_path}/{key}",
                             json={}, headers={
                                 "Authorization": f"Bearer {gw.token}"}) as r:
                ct = r.headers.get("Content-Type", "")
                checks.append(("a write to it is refused as a problem "
                               "document: a topic with no scene is in no lane",
                               r.status == 405 and "problem+json" in ct,
                               f"{r.status} {ct}"))
            async with s.get(gw.base + f"{WK}/discovery/depart/v1") as r:
                ct = r.headers.get("Content-Type", "")
                checks.append(("a well-known topic nobody published is a 404 "
                               "problem document, not an empty 200",
                               r.status == 404 and "problem+json" in ct,
                               f"{r.status}"))
            async with s.get(gw.base + f"{WK}/schemas") as r:
                checks.append(("the well-known routes that were there before "
                               "still answer (schemas index)",
                               r.status == 200, f"{r.status}"))
        # A well-known announce has no scene segment, so the scene it
        # belongs to is the one its own manifest_uri names: alone on the
        # four-segment topic, it is still its scene's declared service.
        only = Path(tmp) / "only-four.mcap"
        author(only, (FOUR,))
        st4 = gateway.build_state(only, None)
        decl = gateway.declared_services(st4, "gate-scene")
        checks.append(("an announce published only on the well-known topic is "
                       "its scene's declared service, by its manifest_uri",
                       [d.get("service_id") for d in decl] == [sid]
                       and gateway.declared_coverage(st4, "gate-scene"),
                       f"{decl}"))
        checks.append(("…and the empty scene is not a lane",
                       "" not in st4.scenes, f"{st4.scenes}"))
    return report("four-segment topics", checks,
                  "a four-segment topic is served at its well-known path, "
                  "exactly as a scene-scoped one")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
