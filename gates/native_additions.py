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


def run() -> int:
    checks: list = []
    s1(checks, random.randint(150, 229))
    return report("native additions", checks,
                  "0.7.1's additions do what finding 67 asked")


if __name__ == "__main__":
    sys.exit(run())
