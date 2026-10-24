#!/usr/bin/env python3
"""A crash is never a dispose; a clean stop withdraws what it said it would.

Every case is a separate process, an SDK participant (`gates/_native_child.py`),
watched by SDK readers in this one, on a domain of its own. The cases:

* **SIGKILL.** `kill -9` the participant. Its entity and its announce must reach
  the readers as `Liveliness(alive=False)` once the lease runs out, and
  never as `Dispose`.
* **The control arm.** The same kill with autodispose left at the DDS default,
  which is what three of the four repositories shipped. The readers must see
  `Dispose`. This is what shows the first case can fail: a gate that could not
  see a dispose would pass it for any participant.
* **Clean stop, `on_exit="withdraw"`.** SIGTERM. The entity's terminal sample
  (RETIRED, with the reason) and its `Dispose` arrive adjacent. The announce
  departs: its `Dispose`, then a `Depart`.
* **Clean stop, `on_exit="keep"`.** SIGTERM. The entity is not disposed: its
  writer leaving is `Liveliness(alive=False)`, promptly. The announce departs
  all the same, because the publisher's presence is genuinely ending.
* **The floor.** A stationary publisher writes one pose and keeps it fresh at
  4 Hz: a reader counts republished samples with strictly increasing stamps.
  The control writes once with no floor, and is seen once.

Usage:  gates/native_lifecycle.py
"""
from __future__ import annotations

import os
import random
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                    # noqa: E402

import spatialdds as sd                                       # noqa: E402

CHILD = ROOT / "gates" / "_native_child.py"
SCENE, SVC, ENT = "gate-lifecycle", "svc:gate:child", "ent:gate:robot"
FAST = sd.dds.Profile("GATE_FAST", reliability="BEST_EFFORT",
                      durability="VOLATILE", history="KEEP_LAST", depth=1)


class Watch:
    """SDK readers on a domain, each drained on its own thread into a list."""

    def __init__(self, domain: int, lanes: dict) -> None:
        self.p = sd.participant(SCENE, domain=domain)
        self.events: dict = {name: [] for name in lanes}
        self._stop = threading.Event()
        self._threads = []
        for name, (type_, kw) in lanes.items():
            r = self.p.reader(type_, **kw)
            t = threading.Thread(target=self._drain, args=(r, name), daemon=True)
            t.start()
            self._threads.append(t)

    def _drain(self, r, name) -> None:
        for ev in r.events(timeout=None, stop=self._stop):
            self.events[name].append((time.time(), ev))

    def kinds(self, name: str, key: str = "") -> list:
        return [type(e).__name__ for _t, e in self.events[name]
                if not key or getattr(e, "key", None) in (key, None)]

    def wait_for(self, name: str, pred, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if any(pred(e) for _t, e in list(self.events[name])):
                return True
            time.sleep(0.05)
        return False

    def close(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)
        self.p.close()


def start(mode: str, domain: int) -> subprocess.Popen:
    c = subprocess.Popen([sys.executable, str(CHILD), mode, str(domain)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    deadline = time.time() + 30
    lines = []
    while time.time() < deadline:
        line = c.stdout.readline()
        if not line:
            break
        lines.append(line.strip())
        if line.strip() == "READY":
            c.lines = lines
            return c
    c.kill()
    raise RuntimeError(f"{mode}: the child never became READY: {lines}")


def finish(c: subprocess.Popen) -> list:
    try:
        out, _ = c.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        c.kill()
        out, _ = c.communicate()
    return c.lines + [x for x in (out or "").splitlines() if x]


def is_(kind, key=ENT):
    return lambda e: type(e).__name__ == kind and getattr(e, "key", "") == key


def run_entity_case(mode: str, how: str, wait: float) -> tuple:
    domain = random.randint(150, 229)
    w = Watch(domain, {"entity": ("model_entity", {"qos_profile": "MAP_META"}),
                       "announce": ("Announce", {}),
                       "depart": ("Depart", {})})
    time.sleep(1.5)
    c = start(mode, domain)
    seen = w.wait_for("entity", is_("Sample"), 10)
    t0 = time.time()
    os.kill(c.pid, signal.SIGKILL if how == "kill" else signal.SIGTERM)
    out = finish(c)
    if how == "kill":
        w.wait_for("entity", lambda e: type(e).__name__ in ("Liveliness",
                                                            "Dispose"), wait)
        w.wait_for("announce", lambda e: type(e).__name__ in ("Liveliness",
                                                              "Dispose"), 5)
    else:
        w.wait_for("depart", lambda e: type(e).__name__ == "Sample", wait)
        # The entity's own outcome, timed: a Dispose for "withdraw", writer
        # liveliness for "keep". A keep that only ended at the lease would be
        # a close that never unregistered.
        w.wait_for("entity", lambda e: type(e).__name__ in ("Liveliness",
                                                            "Dispose"), wait)
    took = time.time() - t0
    w.close()
    return w, seen, out, took


def main() -> int:
    checks: list = []

    w, seen, out, took = run_entity_case("kill", "kill", 30)
    ent, ann = w.kinds("entity", ENT), w.kinds("announce", SVC)
    checks.append(("SIGKILL: the reader had the entity first", seen, f"{out}"))
    checks.append((f"SIGKILL: the entity reaches the reader as "
                   f"Liveliness(alive=False) ({took:.1f} s, the lease)",
                   "Liveliness" in ent, f"{ent}"))
    checks.append(("SIGKILL: and never as Dispose, entity or announce",
                   "Dispose" not in ent and "Dispose" not in ann,
                   f"entity {ent}; announce {ann}"))
    checks.append(("SIGKILL: the announce, too, is liveliness and not removal",
                   "Liveliness" in ann, f"{ann}"))

    w, seen, out, took = run_entity_case("kill-autodispose", "kill", 30)
    ent = w.kinds("entity", ENT)
    checks.append(("control arm: the plant took (autodispose at the DDS "
                   "default)", any("PLANT" in x for x in out), f"{out[:2]}"))
    checks.append((f"control arm: the same kill with the DDS default IS a "
                   f"Dispose ({took:.1f} s), so the case above can fail",
                   "Dispose" in ent, f"{ent}"))

    w, seen, out, took = run_entity_case("withdraw", "term", 15)
    evs = [e for _t, e in w.events["entity"] if getattr(e, "key", "") == ENT]
    kinds = [type(e).__name__ for e in evs]
    try:
        i = kinds.index("Dispose")
        term, disp = evs[i - 1], evs[i]
        adjacent = (type(term).__name__ == "Sample"
                    and term.data.state.name == "RETIRED")
        reason = disp.reason
        sreason = term.data.state_reason
    except (ValueError, IndexError):
        adjacent, reason, sreason = False, "", ""
    checks.append(("clean stop, withdraw: the child closed cleanly",
                   "CLOSED" in out, f"{out}"))
    checks.append(("clean stop, withdraw: a RETIRED terminal sample, "
                   "immediately followed by its Dispose", adjacent, f"{kinds}"))
    checks.append(("…the Dispose carries the reason, and so does the terminal "
                   "sample's own state_reason",
                   reason == "the participant stopped" and sreason == reason,
                   f"{reason!r} / {sreason!r}"))
    ann = w.kinds("announce", SVC)
    dep = [e for _t, e in w.events["depart"]
           if type(e).__name__ == "Sample"]
    checks.append(("clean stop: the announce departs, its Dispose then a "
                   "Depart for the service",
                   "Dispose" in ann and dep
                   and dep[0].data.service_id == SVC, f"{ann} / {len(dep)}"))

    w, seen, out, took = run_entity_case("keep", "term", 15)
    ent, ann = w.kinds("entity", ENT), w.kinds("announce", SVC)
    checks.append(("clean stop, keep: the entity is not disposed",
                   "Dispose" not in ent and "CLOSED" in out, f"{ent}"))
    checks.append(("…its writer leaving is Liveliness(alive=False), promptly "
                   "(a clean unregister, not a lease)",
                   "Liveliness" in ent and took < 10, f"{ent} in {took:.1f} s"))
    checks.append(("…and the announce departs all the same",
                   "Dispose" in ann, f"{ann}"))

    for mode, want in (("floor", "floor"), ("floor-none", "none")):
        domain = random.randint(150, 229)
        wt = Watch(domain, {"pose": ("model_pose", {"qos_profile": FAST})})
        time.sleep(1.5)
        c = start(mode, domain)
        time.sleep(3.0)
        os.kill(c.pid, signal.SIGTERM)
        finish(c)
        wt.close()
        poses = [e.data for _t, e in wt.events["pose"]
                 if type(e).__name__ == "Sample"]
        stamps = [(int(p.stamp.sec), int(p.stamp.nanosec)) for p in poses]
        rising = all(a < b for a, b in zip(stamps, stamps[1:]))
        if want == "floor":
            checks.append((f"floor: one write, kept fresh at 4 Hz: {len(poses)} "
                           f"samples in about 3 s, stamps strictly rising",
                           len(poses) >= 8 and rising, f"{stamps[:4]}"))
        else:
            checks.append((f"floor control: no floor, one write, seen "
                           f"{len(poses)} time(s)", len(poses) == 1, ""))
    return report("native lifecycle", checks,
                  "a crash is liveliness, a clean stop withdraws what it said, "
                  "and the floor keeps a still publisher fresh")


if __name__ == "__main__":
    sys.exit(main())
