#!/usr/bin/env python3
"""What 0.7.1 adds for programs that own their loop, and lanes outside a scene.

Each check is one of finding 67's filings, by number, held against a bus on a
domain of its own: a writer participant and a reader participant in this
process.

* **S1, `Reader.take()`.** It never blocks: a fresh latched reader's first
  call returns at once, before the replay has settled, with no marker. Taken
  in a loop, the replay comes out as every held instance, then exactly one
  `EndOfReplay` carrying that count, then live samples. A VOLATILE lane's
  marker comes on the first call, with `replayed=0`. `events()` on a second
  reader yields the same sequence, because it is `take()` in a loop.
* **S2, `topic=` and `history=`.**
  - A reader in one scene reads another scene's entity lane by its full
    name. A four-segment GeoPose lane carries a sample end to end.
  - A writer on the VPS query lane, a name the spec gives only by example,
    is announced under exactly that name.
  - Five names are refused: six segments, a well-known topic by name, a
    templated type off its template, `topic=` with `stream=`, and a message
    type with `topic=`.
  - `history=` holds: a burst of ten poses on one key reaches a depth-1
    reader as one, a `KEEP_ALL` reader as ten, and a depth-3 reader as three.

Usage:  gates/native_additions.py
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                    # noqa: E402

import spatialdds as sd                                       # noqa: E402

SCENE, SVC = "gate-additions", "svc:gate:additions"
FRAME = sd.frame("map/gate", convention="ENU", meters_per_unit=1.0)
FAST = sd.dds.Profile("GATE_FAST", reliability="BEST_EFFORT",
                      durability="VOLATILE", history="KEEP_LAST", depth=1)


def entity(eid: str, x: float = 0.0) -> dict:
    return sd.records.entity(eid, frame_ref=FRAME, source_id=SVC,
                             position=(x, 0, 0))


def kinds(evs: list) -> list:
    out = []
    for e in evs:
        if isinstance(e, sd.Sample):
            out.append(f"sample:{e.key}")
        elif isinstance(e, sd.EndOfReplay):
            out.append(f"end:{e.replayed}")
        else:
            out.append(type(e).__name__)
    return out


def drain(r, until: float, stop_after_marker_s: float = 0.0) -> list:
    """take() in a loop until a deadline, or a while after the marker."""
    out, marked_at = [], None
    while time.time() < until:
        evs = r.take()
        out += evs
        if marked_at is None and any(isinstance(e, sd.EndOfReplay) for e in evs):
            marked_at = time.time()
        if marked_at is not None and time.time() - marked_at >= stop_after_marker_s:
            break
        time.sleep(0.02)
    return out


def s1(checks: list, domain: int) -> None:
    with sd.participant(SCENE, domain=domain, service_id=SVC) as pw, \
            sd.participant(SCENE, domain=domain) as pr:
        w = pw.writer("model_entity", qos_profile="MAP_META", on_exit="keep")
        for i in range(3):
            w.write(entity(f"ent:gate:{i}", i))

        r = pr.reader("model_entity", qos_profile="MAP_META", settle_s=0.5)
        t0 = time.time()
        first = r.take()
        took = time.time() - t0
        checks.append((f"S1: a fresh latched reader's first take() returns at "
                       f"once ({took * 1000:.1f} ms), before the replay has "
                       f"settled, with no marker",
                       took < 0.25 and not any(isinstance(e, sd.EndOfReplay)
                                               for e in first),
                       f"{took:.3f}s {kinds(first)}"))

        seq = first + drain(r, time.time() + 15, stop_after_marker_s=0.3)
        ks = kinds(seq)
        ends = [k for k in ks if k.startswith("end:")]
        pre = ks[:ks.index(ends[0])] if ends else ks
        checks.append(("S1: taken in a loop, the replay is the three held "
                       "instances, then exactly one EndOfReplay counting them",
                       ends == ["end:3"] and sorted(pre) == [
                           f"sample:ent:gate:{i}" for i in range(3)],
                       f"{ks}"))

        w.write(entity("ent:gate:late", 9))
        live = drain(r, time.time() + 5, stop_after_marker_s=0)
        live += drain(r, time.time() + 1.0)
        checks.append(("S1: a sample written after the marker is live, and "
                       "no second marker comes",
                       "sample:ent:gate:late" in kinds(live)
                       and not any(k.startswith("end:") for k in kinds(live)),
                       f"{kinds(live)}"))

        v = pr.reader("model_pose", qos_profile=FAST)
        vfirst = v.take()
        checks.append(("S1: a VOLATILE lane's marker comes on the first take(), "
                       "replayed=0",
                       kinds(vfirst) == ["end:0"], f"{kinds(vfirst)}"))

        # History that arrives late: the reader is already taking when a
        # second writer joins, inside the settle window. A marker that fired
        # before the matched set settled would count none of it.
        late = SCENE + "-late"
        prl = sd.participant(late, domain=domain)
        lr = prl.reader("model_entity", qos_profile="MAP_META", settle_s=0.8)
        early = lr.take()
        with sd.participant(late, domain=domain,
                            service_id=SVC + ":late") as pl:
            lw = pl.writer("model_entity", qos_profile="MAP_META",
                           on_exit="keep")
            for i in range(2):
                lw.write(entity(f"ent:gate:late{i}", i))
            seq = early + drain(lr, time.time() + 15, stop_after_marker_s=0)
        prl.close()
        lk = kinds(seq)
        checks.append(("S1: history from a writer that joins while the reader "
                       "is already taking is replay, counted, before the marker",
                       [k for k in lk if k.startswith("end:")] == ["end:2"]
                       and lk.index("end:2") == 2, f"{lk}"))

        e = pr.reader("model_entity", qos_profile="MAP_META", settle_s=0.5)
        got = []
        deadline = time.time() + 15
        for ev in e.events(timeout=0.5):
            got.append(ev)
            if time.time() > deadline:
                break
        ek = kinds(got)
        eend = [k for k in ek if k.startswith("end:")]
        checks.append(("S1: events() on a second reader yields the same "
                       "replay: every held instance, then one marker",
                       eend == ["end:4"] and sorted(ek[:ek.index(eend[0])]) ==
                       sorted([f"sample:ent:gate:{i}" for i in range(3)]
                              + ["sample:ent:gate:late"]),
                       f"{ek}"))


def s2(checks: list, domain: int) -> None:
    other = SCENE + "-other"
    with sd.participant(other, domain=domain, service_id=SVC + ":other") as pb, \
            sd.participant(SCENE, domain=domain, service_id=SVC) as pa:
        bw = pb.writer("model_entity", qos_profile="MAP_META", on_exit="keep")
        bw.write(entity("ent:gate:elsewhere"))
        far = pa.reader("model_entity", qos_profile="MAP_META", settle_s=0.5,
                        topic=f"spatialdds/{other}/world/entity/v1")
        got = kinds(drain(far, time.time() + 15))
        checks.append(("S2: a reader in one scene reads another scene's entity "
                       "lane by its full name",
                       far.topic == f"spatialdds/{other}/world/entity/v1"
                       and "sample:ent:gate:elsewhere" in got, f"{got}"))

        lane = "spatialdds/geo/geopose/v1"
        gw = pa.writer("geopose", topic=lane)
        gr = pb.reader("geopose", topic=lane)
        deadline, seen = time.time() + 10, []
        while time.time() < deadline and not any(
                isinstance(e, sd.Sample) for e in seen):
            if gr.matched():
                gw.write(sd.geopose(30.28, -97.73))
            seen += gr.take()
            time.sleep(0.05)
        checks.append(("S2: a four-segment lane with no domain carries a "
                       "GeoPose end to end",
                       gw.topic == lane and any(isinstance(e, sd.Sample)
                                                for e in seen),
                       f"{kinds(seen)}"))

        vw = pa.writer("vps_query", topic="spatialdds/vps/query/v1")
        doc = pa.announce("VPS", coverage=sd.circle(30.28, -97.73, 50),
                          manifest_uri="spatialdds://gate.example/"
                                       f"{SCENE}/service/additions")
        names = sorted(t["name"] for t in doc["topics"])
        vps = [t for t in doc["topics"] if t["name"] == vw.topic]
        checks.append(("S2: a writer on the VPS query lane is announced under "
                       "exactly that name and version",
                       vps and vps[0]["type"] == "vps_query"
                       and vps[0]["version"] == "v1", f"{names}"))

        refused = {}
        for label, call in (
                ("six segments", lambda: pa.reader(
                    "video_frame", topic="spatialdds/ds/vision/cam/frame/v1")),
                ("a well-known topic by name", lambda: pa.reader(
                    "geopose", topic="spatialdds/discovery/announce/v1")),
                ("a templated type off its template", lambda: pa.reader(
                    "model_entity", qos_profile="MAP_META",
                    topic=f"spatialdds/{SCENE}/frames/entity/v1")),
                ("topic= with stream=", lambda: pa.reader(
                    "geopose", stream="geo", topic=lane)),
                ("a message type with topic=", lambda: pa.reader(
                    "ContentAnnounce", topic="spatialdds/x/discovery/content/v1"))):
            try:
                call()
                refused[label] = None
            except sd.dds.ParticipantRefused as e:
                refused[label] = str(e)
        # Each for its own reason: a refusal for some other reason would pass
        # a check that only asked whether it was refused.
        why = {"six segments": "is not a §3.3.1 name",
               "a well-known topic by name": "is the well-known Announce topic",
               "a templated type off its template": "is another topic",
               "topic= with stream=": "stream= would name a second one",
               "a message type with topic=": "so topic= is not accepted"}
        wrong = {k: v for k, v in refused.items() if not v or why[k] not in v}
        checks.append(("S2: refused, each for its own reason: "
                       + "; ".join(refused), not wrong, f"{wrong}"))

        one = sd.dds.Profile("GATE_REPLY", reliability="RELIABLE",
                             durability="VOLATILE", history="KEEP_LAST",
                             depth=1)
        pw = pb.writer("model_pose", qos_profile=one)
        r1 = pa.reader("model_pose", qos_profile=one, topic=pw.topic)
        rall = pa.reader("model_pose", qos_profile=one, topic=pw.topic,
                         history="KEEP_ALL")
        r3 = pa.reader("model_pose", qos_profile=one, topic=pw.topic,
                       history=3)
        deadline = time.time() + 10
        while time.time() < deadline and min(
                r.matched() for r in (r1, rall, r3)) < 1:
            time.sleep(0.05)
        for i in range(10):
            pw.write(sd.records.model_pose("ent:gate:burst", position=(i, 0, 0),
                                           source_id=SVC + ":other"))
            time.sleep(0.02)
        time.sleep(1.0)
        n = {k: sum(isinstance(e, sd.Sample) for e in r.take())
             for k, r in (("depth 1", r1), ("KEEP_ALL", rall), ("depth 3", r3))}
        checks.append((f"S2: history= holds: a ten-pose burst on one key is "
                       f"{n['depth 1']} / {n['KEEP_ALL']} / {n['depth 3']} at "
                       f"depth 1 / KEEP_ALL / depth 3",
                       n == {"depth 1": 1, "KEEP_ALL": 10, "depth 3": 3},
                       f"{n}"))


def arm(checks: list, name: str, fn) -> None:
    """One arm on a domain of its own; whatever it raises is a reported FAIL."""
    try:
        fn(checks, random.randint(150, 229))
    except Exception as e:
        checks.append((f"{name}: the arm ran to its end", False,
                       f"{type(e).__name__}: {e}"))


def run() -> int:
    checks: list = []
    arm(checks, "S1", s1)
    arm(checks, "S2", s2)
    return report("native additions", checks,
                  "0.7.1's additions do what finding 67 asked")


if __name__ == "__main__":
    sys.exit(run())
