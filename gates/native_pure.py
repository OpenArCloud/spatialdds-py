#!/usr/bin/env python3
"""The frame arithmetic and the recency rules, checked by composing them.

Both are pure Python, so this gate needs no extra.

**Frames.** An inverted or mis-scaled transform produces well-formed,
plausible numbers. It is caught by composing, not by reading.

* The geodetic round trip closes to a micrometre over a spread of the globe.
* The direction is checked by construction. A point 100 m east of a frame's
  origin lands east of it on the earth, 100 m up lands 100 m higher, and both
  hold where east and north swap sign (the southern and western hemispheres).
* A point composed from one frame into another and back is where it started.
* A missing georeference is a reason, never a default.
* `frame_problem` refuses a uuid its name does not derive to, a non-metric
  frame and a `SCALE_UNKNOWN` one (§2.13), and accepts a metric one.

**Recency.** A key's idle threshold follows its own cadence:

* `max(floor, 3 × gap)`, with the floor alone until two updates exist;
* the latched record is due every Nth update;
* a still key is reported idle exactly once;
* a key updating faster than the floor is never idle between updates.

That last rule is the 94-false-flush failure the demo measured, as a check.

Usage:  gates/native_pure.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                    # noqa: E402

import spatialdds as sd                                       # noqa: E402

F = sd.frames


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    worst = 0.0
    for lat in (-89.0, -45.5, -12.0, 0.0, 30.2849, 51.5, 89.0):
        for lon in (-179.9, -97.7341, 0.0, 2.35, 139.7):
            for h in (-50.0, 0.0, 150.0, 8848.0):
                t, _q = F.enu_to_ecef_pose(lat, lon, h)
                la, lo, hh = F.geodetic_of(t)
                # metres: latitude and longitude errors scaled by the radius
                err = max(abs(la - lat) * 111_320, abs(
                    (lo - lon + 180) % 360 - 180) * 111_320 *
                    math.cos(math.radians(lat)), abs(hh - h))
                worst = max(worst, err)
    checks.append((f"enu_to_ecef_pose then geodetic_of closes over 140 points "
                   f"(worst {worst * 1e6:.3f} µm)", worst < 1e-6, f"{worst}"))

    bad = []
    for lat, lon in ((30.2849, -97.7341), (-33.86, 151.21), (64.1, -21.9)):
        pose = F.enu_to_ecef_pose(lat, lon, 100.0)
        e_lat, e_lon, e_h = F.geodetic_of(F.to_earth(pose, (100.0, 0.0, 0.0)))
        n_lat, n_lon, _ = F.geodetic_of(F.to_earth(pose, (0.0, 100.0, 0.0)))
        u_lat, u_lon, u_h = F.geodetic_of(F.to_earth(pose, (0.0, 0.0, 100.0)))
        if not (e_lon > lon and abs(e_lat - lat) < 1e-5):
            bad.append(f"east at {lat},{lon}")
        if not (n_lat > lat and abs(n_lon - lon) < 1e-5):
            bad.append(f"north at {lat},{lon}")
        if not (abs(u_h - 200.0) < 1e-6 and abs(u_lat - lat) < 1e-9):
            bad.append(f"up at {lat},{lon}: {u_h}")
    checks.append(("east is east, north is north and up is up, in both "
                   "hemispheres: the transform maps local into earth-fixed",
                   not bad, "; ".join(bad)))

    a = F.enu_to_ecef_pose(30.2849, -97.7341, 150.0)
    b = F.enu_to_ecef_pose(30.2860, -97.7350, 152.0)
    geo = {"map/a": a, "map/b": b}
    p = (11.7, -14.3, -1.4)
    in_b, why = F.compose_point(p, "map/a", "map/b", geo)
    back, _ = F.compose_point(in_b, "map/b", "map/a", geo)
    checks.append(("a point composed a → b → a is where it started (1e-6 m)",
                   why is None and max(abs(x - y) for x, y in zip(back, p))
                   < 1e-6, f"{back}"))
    same, _ = F.compose_point(p, "map/a", "map/a", {})
    checks.append(("composing within one frame needs no georeference",
                   same == list(p), ""))
    none, why = F.compose_point(p, "map/a", "map/c", geo)
    checks.append(("a missing georeference is (None, reason), never a "
                   "defaulted position", none is None and "map/c" in why, why))

    ok = sd.frame("map/a", uuid=F.frame_uuid("map/a"), convention="ENU",
                  meters_per_unit=1.0)
    clash = sd.frame("map/a", uuid=F.frame_uuid("map/b"))
    feet = sd.frame("map/a", meters_per_unit=0.3048)
    unknown = sd.frame("map/a", meters_per_unit=1.0,
                       scale_status="SCALE_UNKNOWN")
    checks.append(("frame_problem accepts a metric frame whose uuid its name "
                   "derives to", F.frame_problem(ok) is None, ""))
    checks.append(("…and refuses a borrowed uuid, a frame in feet, and a "
                   "SCALE_UNKNOWN frame (§2.13)",
                   all(F.frame_problem(x) for x in (clash, feet, unknown)),
                   f"{[F.frame_problem(x) for x in (clash, feet, unknown)]}"))

    r = sd.Recency()
    checks.append(("one update is not a cadence: the floor until two",
                   (r.observed("k", 0.0), r.threshold("k")) == (False, 1.0), ""))
    for i in range(1, 4):
        r.observed("k", 0.5 * i)
    checks.append(("a key updating every 0.5 s idles after 3 × its gap "
                   "(1.5 s), which is above the 1 s floor",
                   abs(r.threshold("k") - 1.5) < 1e-9, f"{r.threshold('k')}"))
    slow = sd.Recency()
    for i in range(5):
        slow.observed("s", 2.0 * i)
    checks.append(("a key updating every 2 s idles after 3 × its own gap "
                   "(6 s), so its own rhythm is not a stop",
                   abs(slow.threshold("s") - 6.0) < 1e-9
                   and slow.idle(8.0 + 5.9) == [] and slow.idle(8.0 + 6.1) == [],
                   f"{slow.threshold('s')}"))
    lat = sd.Recency(latch_every=5)
    seq = [lat.observed("d", float(i)) for i in range(10)]
    checks.append(("the latched record is due every 5th update",
                   seq == [False] * 4 + [True] + [False] * 4 + [True], f"{seq}"))
    st = sd.Recency()
    st.observed("x", 0.0)
    st.observed("x", 0.5)
    checks.append(("a still key with pending changes is idle once, then clean",
                   st.idle(10.0) == ["x"] and st.idle(20.0) == [], ""))
    fast = sd.Recency()
    flushes = []
    for i in range(100):
        fast.observed("duck", 1.5 * i)
        flushes += [i] * len(fast.idle(1.5 * i + 1.4))
    checks.append(("a mover updating every 1.5 s is idle between updates only "
                   "before its cadence exists: one learning flush after the "
                   "first update, then never (the demo's 94 false flushes, as "
                   "a check)", flushes == [0], f"flushes after updates {flushes}"))
    try:
        sd.Recency(smoothing=1.0)
        refused = False
    except ValueError:
        refused = True
    checks.append(("a smoothing that never learns is refused", refused, ""))
    return report("frames and recency", checks,
                  "the arithmetic closes, the direction is right, and idle "
                  "follows each key's own cadence")


if __name__ == "__main__":
    sys.exit(main())
