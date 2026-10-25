#!/usr/bin/env python3
"""What 0.7.x adds for programs that own their loop, lanes outside a scene, and their own types.

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
* **S3, the announce.**
  - Placement `transforms=` reach a reader on the announce.
  - Two services announced from one participant are two instances, each
    with its own lanes, and both depart on `close()`: two disposes, then two
    `Depart`s.
  - A second service is refused while the first claims "every writer".
  - A relayed announce carries the asserter's members and a `relayed_by`
    naming the relay.
  - The relay refuses its own service, and an announce already relayed.
  - The relay's `close()` reaches readers as liveliness ending, never as a
    `Dispose`. Its `withdraw()` is a `Dispose`, recorded with the relay's
    name in the reason.
* **S4, `ignore_local=`.** A participant that writes and reads one lane, with
  `ignore_local=True`, hears another participant's write and not its own.
  The control reader, without it, hears both.
* **S5, `matched()` and `wait_matched()`.** A writer with no reader is
  matched with none, and `wait_matched()` returns False at its timeout. Once
  a reader exists, it returns True and `matched()` counts it.
* **S9, `class_for()`.** A registered name, a well-known message and a dotted
  identity each resolve to the generated class. An unknown name is refused,
  saying which forms were tried.
* **S10, `extend()`.** A deployment's own type, defined here with IDL that
  includes a pinned file.
  - Before it is registered, `to_json` refuses it, naming `extend()`.
  - After, it round-trips through N.1, its key is its own `@key`, and
    `class_for` names it.
  - A latched writer opened by its name takes `on_exit=` because the type is
    keyed, and validation refuses a NaN.
  - A reader sees the sample, then the `Dispose` of a withdrawal, under its
    key.
  - The participant's recording embeds the extension's IDL, and the gateway
    serves the lane from it.
  - Refused: a name with no namespace, a registered name, a pinned type,
    IDL including an unpinned file, and the same name with other IDL.
    Registering again with identical arguments is a no-op.

Usage:  gates/native_additions.py
"""
from __future__ import annotations

import random
import sys
import tempfile
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


def placement() -> dict:
    return {"from": FRAME, "to": sd.earth_fixed(),
            "pose": {"t": [1.0, 2.0, 3.0], "q": [0.0, 0.0, 0.0, 1.0]},
            "stamp": sd.stamp(), "has_validity": False,
            "validity": {"from": {"sec": "0", "nanosec": 0}, "seconds": 0}}


def announces(r, until: float, want) -> list:
    out = []
    while time.time() < until and not want(out):
        out += r.take()
        time.sleep(0.05)
    return out


def s3(checks: list, domain: int) -> None:
    from spatialdds.dds import RELAYED_BY
    cov = sd.circle(30.28, -97.73, 50)
    watch = sd.participant(SCENE, domain=domain)
    ra = watch.reader("Announce", settle_s=0.3)
    rd = watch.reader("Depart")
    rec = None
    try:
        with sd.participant(SCENE, domain=domain, service_id=SVC,
                            authority="gate.example") as p:
            ent = p.writer("model_entity", qos_profile="MAP_META",
                           on_exit="keep")
            pose = p.writer("model_pose", qos_profile=FAST)
            # The first service claims "every writer", so a second one makes
            # that ambiguous and is refused, even though it names its own lanes.
            p.announce("OTHER", coverage=cov, transforms=[placement()])
            try:
                p.announce("OTHER", coverage=cov, service_id=SVC + ":b",
                           topics=[pose])
                refused = None
            except sd.dds.ParticipantRefused as e:
                refused = str(e)
            checks.append(("S3: a second service is refused while an announce "
                           "claims 'every writer'",
                           refused is not None
                           and "names its lanes with topics=" in refused,
                           f"{refused}"))
            p.announce("OTHER", coverage=cov, transforms=[placement()],
                       topics=[ent])
            p.announce("OTHER", coverage=cov, service_id=SVC + ":b",
                       topics=[pose])
            evs = announces(ra, time.time() + 15, lambda o: len(
                {e.key for e in o if isinstance(e, sd.Sample)}) >= 2)
            got = {e.key: e.data for e in evs if isinstance(e, sd.Sample)}
            a, b = got.get(SVC), got.get(SVC + ":b")
            checks.append(("S3: placement transforms reach a reader on the "
                           "announce",
                           a is not None and len(a.transforms) == 1
                           and a.transforms[0].to.fqn == "earth-fixed",
                           f"{a.transforms if a else None}"))
            checks.append(("S3: two services from one participant are two "
                           "instances, each with its own lanes",
                           a is not None and b is not None
                           and [t.name for t in a.topics] == [ent.topic]
                           and [t.name for t in b.topics] == [pose.topic],
                           f"{ {k: [t.name for t in v.topics] for k, v in got.items()} }"))
        evs = announces(ra, time.time() + 10, lambda o: len(
            [e for e in o if isinstance(e, sd.Dispose)]) >= 2)
        deps = announces(rd, time.time() + 10, lambda o: len(
            [e for e in o if isinstance(e, sd.Sample)]) >= 2)
        checks.append(("S3: both depart on close(): two disposes, two Departs",
                       sorted(e.key for e in evs if isinstance(e, sd.Dispose))
                       == sorted([SVC, SVC + ":b"])
                       and sorted(e.key for e in deps
                                  if isinstance(e, sd.Sample))
                       == sorted([SVC, SVC + ":b"]),
                       f"{kinds(evs)} / {kinds(deps)}"))

        heard = sd.service_announce(
            "svc:gate:elsewhere", "VPS", coverage=cov,
            authority="elsewhere.example", scene="far", name="heard over MQTT")
        relay_id = SVC + ":relay"
        with tempfile.TemporaryDirectory() as tmp:
            with sd.participant(SCENE, domain=domain, service_id=relay_id,
                                authority="gate.example") as rp:
                rec = rp.record(Path(tmp) / "relay.mcap")
                relay = rp.relay()
                relay.write(heard)
                evs = announces(ra, time.time() + 10, lambda o: any(
                    isinstance(e, sd.Sample) and e.key == "svc:gate:elsewhere"
                    for e in o))
                s_ = [e.data for e in evs if isinstance(e, sd.Sample)
                      and e.key == "svc:gate:elsewhere"]
                hints = {h.key: h.value for h in s_[-1].hints} if s_ else {}
                checks.append(("S3: a relayed announce keeps the asserter's "
                               "members and names the relay in relayed_by",
                               s_ and s_[-1].name == "heard over MQTT"
                               and hints.get(RELAYED_BY) == relay_id, f"{hints}"))
                why = {}
                for label, doc in (("its own service", sd.service_announce(
                        relay_id, "OTHER", coverage=cov,
                        authority="gate.example", scene=SCENE)),
                        ("an announce already relayed", dict(
                            heard, hints=[{"key": RELAYED_BY,
                                           "value": "svc:someone"}]))):
                    try:
                        relay.write(doc)
                        why[label] = None
                    except sd.dds.ParticipantRefused as e:
                        why[label] = str(e)
                checks.append(("S3: the relay refuses " + " and ".join(why),
                               why["its own service"] and "announce() it" in
                               why["its own service"]
                               and why["an announce already relayed"]
                               and "already relayed by svc:someone" in
                               why["an announce already relayed"], f"{why}"))
                second = sd.service_announce(
                    "svc:gate:second", "VPS", coverage=cov,
                    authority="elsewhere.example", scene="far")
                relay.write(second)
                announces(ra, time.time() + 10, lambda o: any(
                    isinstance(e, sd.Sample) and e.key == "svc:gate:second"
                    for e in o))
                relay.withdraw("svc:gate:second", reason="it departed upstream")
                evs = announces(ra, time.time() + 10, lambda o: any(
                    isinstance(e, sd.Dispose) for e in o))
                checks.append(("S3: the relay's withdraw() is a Dispose of the "
                               "relayed copy",
                               [e.key for e in evs if isinstance(e, sd.Dispose)]
                               == ["svc:gate:second"], f"{kinds(evs)}"))
            evs = announces(ra, time.time() + 12, lambda o: any(
                isinstance(e, (sd.Liveliness, sd.Dispose))
                and e.key == "svc:gate:elsewhere" for e in o))
            ends = [type(e).__name__ for e in evs
                    if isinstance(e, (sd.Liveliness, sd.Dispose))
                    and e.key == "svc:gate:elsewhere"]
            checks.append(("S3: the relay's close() is liveliness ending for "
                           "what it carried, never a Dispose",
                           ends == ["Liveliness"], f"{ends}"))
            reasons = [md.metadata.get("reason", "") for md in _metadata(
                Path(tmp) / "relay.mcap")]
            checks.append(("S3: the withdrawal is recorded with the relay's "
                           "name in its reason",
                           any(r.startswith(f"relayed by {relay_id}:")
                               for r in reasons), f"{reasons}"))
    finally:
        watch.close()


def s4_s5_s9(checks: list, domain: int) -> None:
    lane = f"spatialdds/{SCENE}/bridge/geopose/v1"
    with sd.participant(SCENE, domain=domain, service_id=SVC) as me, \
            sd.participant(SCENE, domain=domain, service_id=SVC + ":far") as far:
        mine = me.writer("geopose", topic=lane)
        # KEEP_ALL: POSE_RT is KEEP_LAST(1), and both writes are one
        # instance, so a depth-1 reader would hold only the second.
        deaf = me.reader("geopose", topic=lane, ignore_local=True,
                         history="KEEP_ALL")
        hears = me.reader("geopose", topic=lane, history="KEEP_ALL")
        theirs = far.writer("geopose", topic=lane)
        for w in (mine, theirs):
            w.wait_matched(2, timeout=10)
        mine.write(sd.geopose(30.0, -97.0))
        theirs.write(sd.geopose(31.0, -97.0))
        time.sleep(1.0)
        lat = {name: sorted(round(e.data.lat_deg) for e in r.take()
                            if isinstance(e, sd.Sample))
               for name, r in (("ignore_local", deaf), ("control", hears))}
        checks.append(("S4: with ignore_local, a participant hears another's "
                       "write and not its own; the control hears both",
                       lat == {"ignore_local": [31], "control": [30, 31]},
                       f"{lat}"))

        lonely = me.writer("geopose", topic=f"spatialdds/{SCENE}/nobody/geopose/v1")
        t0 = time.time()
        none = lonely.wait_matched(timeout=0.5)
        waited = time.time() - t0
        before = lonely.matched()
        far.reader("geopose", topic=lonely.topic)
        some = lonely.wait_matched(timeout=10)
        checks.append((f"S5: an unheard writer is matched with none, and "
                       f"wait_matched() is False at its timeout "
                       f"({waited:.2f} s); once a reader exists it is True",
                       before == 0 and none is False and 0.45 <= waited < 2
                       and some is True and lonely.matched() == 1,
                       f"before={before} none={none} some={some} "
                       f"after={lonely.matched()}"))

    resolved = {n: sd.dds.class_for(n).__name__
                for n in ("model_entity", "Announce", "spatial.core.GeoPose")}
    try:
        sd.dds.class_for("oarc.never_registered")
        unknown = None
    except sd.dds.RegistryError as e:
        unknown = str(e)
    checks.append(("S9: class_for resolves a registered name, a well-known "
                   "message and a dotted identity, and refuses an unknown name",
                   resolved == {"model_entity": "Entity", "Announce": "Announce",
                                "spatial.core.GeoPose": "GeoPose"}
                   and unknown and "not a registered type name" in unknown,
                   f"{resolved} / {unknown}"))


def s10(checks: list, domain: int) -> None:
    import math
    from spatialdds.server import gateway
    from _ext_type import EXT_IDL, Note, Reading
    doc = {"reading_id": "r1", "label": "gate", "value": 2.5,
           "frame_ref": FRAME}
    try:
        sd.dds.to_json(Reading("r0", "x", 1.0, sd.dds.from_json(
            sd.dds.class_for("spatial.common.FrameRef"), FRAME)))
        before = None
    except sd.dds.RegistryError as e:
        before = str(e)
    checks.append(("S10: before extend(), to_json refuses the type, naming "
                   "extend()", before is not None and "sd.dds.extend" in before,
                   f"{before}"))

    unkeyed_before = sd.dds.key_of(Reading("r0", "x", 1.0, sd.dds.from_json(
        sd.dds.class_for("spatial.common.FrameRef"), FRAME)))
    ext = sd.dds.extend("gate.reading", Reading, EXT_IDL)
    # One IDL file can define several types; the second is registered after
    # the first has merged both into the model.
    try:
        note = sd.dds.extend("gate.note", Note, EXT_IDL)
        second = sd.dds.to_json(Note("n1", "hello"))["note_id"] == "n1"
    except sd.dds.RegistryError as e:
        note, second = None, str(e)
    checks.append(("S10: a second type from the same IDL file registers after "
                   "the first", note is not None and second is True,
                   f"{second}"))
    r = sd.dds.from_json(Reading, doc)
    back = sd.dds.to_json(r)
    checks.append(("S10: after it, the type round-trips through N.1, keys on "
                   "its own @key, and class_for names it",
                   back["reading_id"] == "r1" and back["value"] == 2.5
                   and back["frame_ref"]["fqn"] == FRAME["fqn"]
                   and sd.dds.key_of(r) == "r1" and unkeyed_before == ""
                   and sd.dds.class_for("gate.reading") is Reading
                   and sd.dds.class_for("gate_ext.Reading") is Reading,
                   f"{back} key={sd.dds.key_of(r)!r}"))

    refused = {}
    for label, call, why in (
            ("no namespace", lambda: sd.dds.extend("reading", Reading, EXT_IDL),
             "must be namespaced"),
            ("a registered name", lambda: sd.dds.extend(
                "model_entity", Reading, EXT_IDL), "is the spec's"),
            ("a pinned type", lambda: sd.dds.extend(
                "gate.pose", sd.dds.class_for("spatial.core.GeoPose"), EXT_IDL),
             "is in the pinned IDL"),
            ("an unpinned include", lambda: sd.dds.extend(
                "gate.other", Reading, '#include "mine.idl"\n' + EXT_IDL),
             "does not have"),
            ("the same name, other IDL", lambda: sd.dds.extend(
                "gate.reading", Reading, EXT_IDL + "\n// changed\n"),
             "already registered, differently")):
        try:
            call()
            refused[label] = None
        except sd.dds.RegistryError as e:
            refused[label] = str(e) if why in str(e) else f"WRONG: {e}"
    same = sd.dds.extend("gate.reading", Reading, EXT_IDL) is ext
    wrong = {k: v for k, v in refused.items() if not v or v.startswith("WRONG")}
    checks.append(("S10: refused, each for its own reason: "
                   + "; ".join(refused) + "; and identical re-registration "
                   "is a no-op", not wrong and same, f"{wrong} same={same}"))

    latched = sd.dds.Profile("GATE_EXT", reliability="RELIABLE",
                             durability="TRANSIENT_LOCAL", history="KEEP_LAST",
                             depth=1)
    with tempfile.TemporaryDirectory() as tmp:
        rec_path = Path(tmp) / "ext.mcap"
        with sd.participant(SCENE, domain=domain, service_id=SVC) as pw, \
                sd.participant(SCENE, domain=domain) as pr:
            pw.record(rec_path)
            w = pw.writer("gate.reading", stream="ext", qos_profile=latched,
                          on_exit="withdraw")
            w.write(doc)
            nan = dict(doc, reading_id="r2", value=math.nan)
            try:
                w.write(nan)
                bad = None
            except sd.dds.SchemaInvalid as e:
                bad = str(e)
            checks.append(("S10: a latched writer opened by the extension's "
                           "name is keyed (on_exit accepted), and refuses a NaN",
                           w.topic == f"spatialdds/{SCENE}/ext/gate.reading/v1"
                           and w.keys() == ["r1"] and bad is not None,
                           f"{w.topic} {w.keys()} {bad}"))
            rd = pr.reader("gate.reading", stream="ext", qos_profile=latched,
                           settle_s=0.3)
            got = drain(rd, time.time() + 10, stop_after_marker_s=0)
            w.withdraw("r1", reason="the gate is done with it")
            got += drain(rd, time.time() + 3)
            seen = [(type(e).__name__, getattr(e, "key", None)) for e in got
                    if not isinstance(e, sd.EndOfReplay)]
            checks.append(("S10: a reader sees the sample, then the Dispose of "
                           "its withdrawal, under the extension's key",
                           seen[:1] == [("Sample", "r1")]
                           and ("Dispose", "r1") in seen, f"{seen}"))
        st = gateway.build_state(rec_path, None)
        lane = [res for res in st.resources.values()
                if res.topic == f"spatialdds/{SCENE}/ext/gate.reading/v1"]
        served = [ev for ev in st.live.get(lane[0].topic, [])
                  if ev["event"] == "sample"] if lane else []
        checks.append(("S10: the recording embeds the extension's IDL, and the "
                       "gateway serves its lane from it",
                       lane and lane[0].key == "reading_id" and served
                       and served[0]["data"]["label"] == "gate",
                       f"{[r.topic for r in st.resources.values()]}"))


def _metadata(path: Path) -> list:
    from mcap.reader import make_reader
    from spatialdds.server.mcapmeta import DISPOSE_RECORD
    with open(path, "rb") as f:
        return [m for m in make_reader(f).iter_metadata()
                if m.name == DISPOSE_RECORD]


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
    arm(checks, "S3", s3)
    arm(checks, "S4, S5, S9", s4_s5_s9)
    arm(checks, "S10", s10)
    return report("native additions", checks,
                  "0.7.x's additions do what findings 67 and 69 asked")


if __name__ == "__main__":
    sys.exit(run())
