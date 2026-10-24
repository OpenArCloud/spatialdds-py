"""A native participant for gates/native_lifecycle.py to start, stop and kill.

    python3 gates/_native_child.py MODE DOMAIN

Modes: kill, kill-autodispose (the control arm: the DDS default restored),
withdraw, keep, floor, floor-none. It prints READY once it has written, then
runs until it is signalled (or killed).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib" / "spatialdds"))

import spatialdds as sd                                        # noqa: E402

SCENE, SVC, ENT = "gate-lifecycle", "svc:gate:child", "ent:gate:robot"
FAST = sd.dds.Profile("GATE_FAST", reliability="BEST_EFFORT",
                      durability="VOLATILE", history="KEEP_LAST", depth=1)


def main() -> int:
    mode, domain = sys.argv[1], int(sys.argv[2])
    if mode == "kill-autodispose":
        # The control arm: the writer QoS without autodispose=False, which is
        # DDS's default and what three of the four repositories shipped.
        from cyclonedds import qos as _q
        from spatialdds.dds import _participant, qos
        _participant.writer_qos = lambda p, lifespan_sec=None: _q.Qos(
            *qos._common(p, lifespan_sec))
        print("PLANT autodispose left at the DDS default", flush=True)
    with sd.participant(SCENE, domain=domain, service_id=SVC,
                        authority="gate.example") as p:
        frame = sd.frame("map/gate", convention="ENU", meters_per_unit=1.0)
        if mode.startswith("floor"):
            w = p.writer("model_pose", qos_profile=FAST,
                         floor_hz=4.0 if mode == "floor" else None)
            w.write(sd.model_pose(ENT, position=(1, 2, 0), source_id=SVC))
        else:
            on_exit = "withdraw" if mode == "withdraw" else "keep"
            w = p.writer("model_entity", qos_profile="MAP_META", on_exit=on_exit)
            w.write(sd.entity(ENT, frame_ref=frame, source_id=SVC,
                              position=(1, 2, 0)))
        p.announce("OTHER", coverage=sd.circle(30.28, -97.73, 50))
        print("READY", flush=True)
        for _ in p.loop(hz=10):
            pass
        print("STOPPING", flush=True)
    print("CLOSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
