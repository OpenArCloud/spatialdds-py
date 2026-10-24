#!/usr/bin/env python3
"""While the demo still carries its own projection, the two agree to a nanometre.

`spatialdds.frames` is the one copy of the ENU↔ECEF arithmetic, and
SpatialDDS-demo's `plaza.enu_to_ecef_pose` is a second copy until the demo
adopts the SDK. Two copies of this arithmetic drifting apart would look like a
disagreement about where the venue is. So this gate loads the demo's module by
file path, read-only (nothing of the demo's package is imported). It compares
the two at the demo's own venue anchor and over a spread of the globe.

`gates/run_all.py` skips it, with the reason, where there is no
SpatialDDS-demo checkout beside this one. It retires with the demo's copy.

Usage:  gates/native_frames_agree.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                    # noqa: E402

import spatialdds as sd                                       # noqa: E402

PLAZA = ROOT.parent / "SpatialDDS-demo" / "spatialdds_demo" / "plaza.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("_demo_plaza", PLAZA)
    plaza = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plaza)
    anchor = (plaza.VENUE_ANCHOR_LAT_DEG, plaza.VENUE_ANCHOR_LON_DEG,
              plaza.VENUE_ANCHOR_HEIGHT_M)
    points = [anchor] + [(lat, lon, h) for lat in (-60.0, -1.0, 45.0, 80.0)
                         for lon in (-150.0, -30.0, 10.0, 170.0)
                         for h in (0.0, 300.0)]
    worst_t, worst_q = 0.0, 0.0
    for lat, lon, h in points:
        t1, q1 = sd.frames.enu_to_ecef_pose(lat, lon, h)
        t2, q2 = plaza.enu_to_ecef_pose(lat, lon, h)
        worst_t = max(worst_t, *(abs(a - b) for a, b in zip(t1, t2)))
        worst_q = max(worst_q, *(abs(a - b) for a, b in zip(q1, q2)))
    t1, _ = sd.frames.enu_to_ecef_pose(*anchor)
    t2, _ = plaza.enu_to_ecef_pose(*anchor)
    checks = [
        (f"at the demo's venue anchor {anchor[:2]} the translations agree",
         max(abs(a - b) for a, b in zip(t1, t2)) < 1e-9, f"{t1} vs {t2}"),
        (f"over {len(points)} points the translations agree to a nanometre "
         f"(worst {worst_t:.2e} m)", worst_t < 1e-9, ""),
        (f"and the rotations to 1e-12 (worst {worst_q:.2e})",
         worst_q < 1e-12, ""),
    ]
    return report("frames agree with the demo's copy", checks,
                  "one arithmetic, whichever copy a program calls")


if __name__ == "__main__":
    sys.exit(main())
