#!/usr/bin/env python3
"""A validated write costs the validation, not the schema's compilation.

Finding 75: a validated `write()` read all 112 vendored schemas from disk and
compiled one into a validator, per sample. Measured on 0.7.4: 7.04 ms a
`ModelPose` write validated against 0.01 ms with `validate=False`; on this
branch before the fix, 5.11 ms against 0.009 ms. So a 10 Hz publisher of a
few dozen instances spent most of its budget validating, and the demo's
crossing scene wrote its pose lane unvalidated. The write contract
(every write validated, validation cost bounded) makes that a hole, not a
fast mode: the schemas are now read once a process and each type's validator
compiled once, and `validate=False` is gone.

**The budget**, which this gate fails on:

  * a validated `ModelPose` `write()` on the bus, median over 500 writes:
    at most `POSE_BUDGET_MS`, 1.0 ms (a fifth of the cost before the fix,
    about eight times the cost after it on the machine it was set on);
  * the gateway's validation of an `Entity` body (`publish.validate_schema`,
    every `PUT`), median over 500: at most `PUT_BUDGET_MS`, 1.0 ms;
  * 50 calls of `webjson.schemas()` read the vendored schema files once,
    112 reads, not 50 times that.

And what must not change with it:

  * a refusal reads as `jsonschema.validate`'s did: the gateway's refusal
    carries its message, and the cached validator picks its error, message
    and path, which is what the native codec reports;
  * `schemas()` hands each caller its own copy: editing one changes neither
    the next call's answer nor what validation checks against;
  * `Participant.writer` has no `validate=`: passing it is a `TypeError`.

Usage:  gates/write_validation_cost.py
"""
from __future__ import annotations

import pathlib
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import jsonschema                                              # noqa: E402

from harness import report                                     # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.dds import codec                               # noqa: E402
from spatialdds.server import publish, webjson                 # noqa: E402

POSE_BUDGET_MS = 1.0
PUT_BUDGET_MS = 1.0
N = 500
#: Finding 75's measurement on 0.7.4, and this branch's before the fix
#: (c43c274, the same host and method as below), quoted beside the result.
FINDING_75 = {"validated": 7.04, "unvalidated": 0.01}
BEFORE_FIX = {"validated": 5.11, "raw": 0.009}
SVC = "svc:gate:write-cost"
FRAME = sd.frame("map/write-cost", convention="ENU", meters_per_unit=1.0)


def median_ms(fn) -> float:
    fn()
    ts = []
    for _ in range(N):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    return statistics.median(ts) * 1e3


def bus_costs(checks: list, domain: int) -> None:
    with sd.participant("write-cost", domain=domain, service_id=SVC) as p:
        w = p.writer("model_pose", qos_profile="POSE_RT")
        s = w._coerce(sd.model_pose("ent:a", position=(1, 2, 0),
                                    source_id=SVC))
        validated = median_ms(lambda: w.write(s))
        raw = median_ms(lambda: w._dw.write(s))
        try:
            p.writer("model_pose", qos_profile="POSE_RT", validate=False)
            refused = "accepted"
        except TypeError as e:
            refused = f"TypeError: {e}"
    checks.append((f"a validated ModelPose write() costs at most "
                   f"{POSE_BUDGET_MS} ms, median of {N} (finding 75)",
                   validated <= POSE_BUDGET_MS,
                   ""))
    checks[-1] = (checks[-1][0] + f": {validated:.3f} ms, beside "
                  f"{raw:.3f} ms for the same sample written to the bus "
                  f"unvalidated; finding 75 measured {FINDING_75['validated']} "
                  f"against {FINDING_75['unvalidated']} ms on 0.7.4, and "
                  f"{BEFORE_FIX['validated']} against {BEFORE_FIX['raw']} ms "
                  f"on this branch before the fix",
                  checks[-1][1],
                  f"{validated:.3f} ms is over the {POSE_BUDGET_MS} ms budget")
    checks.append(("Participant.writer takes no validate=: there is no "
                   "unvalidated write",
                   refused.startswith("TypeError") and "validate" in refused,
                   refused))


def put_cost(checks: list) -> None:
    doc = sd.entity("ent:a", frame_ref=FRAME, source_id=SVC,
                    position=[0.0, 0.0, 0.0], basis="DECLARED")
    fqn = "spatial.owm.Entity"
    cost = median_ms(lambda: publish.validate_schema(fqn, doc))
    checks.append((f"the gateway validates an Entity body in at most "
                   f"{PUT_BUDGET_MS} ms, median of {N}: {cost:.3f} ms",
                   cost <= PUT_BUDGET_MS,
                   f"{cost:.3f} ms is over the {PUT_BUDGET_MS} ms budget"))


def reads_once(checks: list) -> None:
    schema_dir = webjson.ARTIFACTS / "schemas"
    reads = []
    real = pathlib.Path.read_text

    def counting(self, *a, **k):
        if self.parent == schema_dir:
            reads.append(self.name)
        return real(self, *a, **k)

    files = len(list(schema_dir.glob("*.json")))
    pathlib.Path.read_text = counting
    try:
        # A cold start, where there is a cache to empty: the inverse plant
        # removes it, and must fail this check rather than crash on it.
        for fn in (getattr(webjson, "_vendored_schemas", None),
                   webjson.vendored_validator):
            getattr(fn, "cache_clear", lambda: None)()
        for _ in range(50):
            webjson.schemas()
        for _ in range(50):
            webjson.vendored_validator("spatial.owm.ModelPose")
    finally:
        pathlib.Path.read_text = real
    checks.append((f"50 calls of schemas() and 50 validator lookups read the "
                   f"{files} vendored schema files once each",
                   len(reads) == files and len(set(reads)) == files,
                   f"{len(reads)} reads of {len(set(reads))} distinct files"))


def unchanged(checks: list) -> None:
    fqn = "spatial.owm.Entity"
    bad = {k: v for k, v in sd.entity(
        "ent:bad", frame_ref=FRAME, source_id=SVC, position=[0.0, 0.0, 0.0],
        basis="DECLARED").items() if k != "layer"}
    try:
        jsonschema.validate(bad, webjson.schemas()[fqn])
        want = None
    except jsonschema.ValidationError as e:
        want = e.message
    try:
        publish.validate_schema(fqn, bad)
        got = None
    except publish.Rejected as e:
        got = e.detail if hasattr(e, "detail") else str(e)
    checks.append(("a refused body reads as jsonschema.validate's refusal "
                   "did: the same message",
                   want is not None and got is not None and want in got,
                   f"jsonschema: {want!r}; gateway: {got!r}"))

    # The codec and the gateway both refuse through `webjson.first_error`;
    # it must pick the error `jsonschema.validate` raised, message and path.
    pose = sd.model_pose("ent:a", position=(1, 2, 0), source_id=SVC)
    cases = [("spatial.owm.Entity", bad),
             ("spatial.owm.ModelPose", dict(pose, stamp={"sec": "x",
                                                         "nanosec": 0})),
             ("spatial.owm.ModelPose", dict(pose, stamp={"sec": "1",
                                                         "nanosec": -1}))]
    differ = []
    for f, d in cases:
        try:
            jsonschema.validate(d, webjson.schemas()[f])
            ref = None
        except jsonschema.ValidationError as e:
            ref = (e.message, list(e.absolute_path))
        e = webjson.first_error(webjson.vendored_validator(f), d)
        mine_ = None if e is None else (e.message, list(e.absolute_path))
        if ref is None or ref != mine_:
            differ.append((f, ref, mine_))
    checks.append(("the cached validator refuses with jsonschema.validate's "
                   "own error, message and path, on three invalid documents",
                   not differ, f"{differ}"))

    mine = webjson.schemas()
    mine["spatial.owm.Entity"]["required"] = []
    mine.pop("spatial.owm.ModelPose", None)
    fresh = webjson.schemas()
    still = "spatial.owm.ModelPose" in fresh and bool(
        fresh["spatial.owm.Entity"].get("required"))
    try:
        publish.validate_schema(fqn, bad)
        holds = False
    except publish.Rejected:
        holds = True
    checks.append(("schemas() hands each caller its own copy: editing one "
                   "changes neither the next answer nor what is validated",
                   still and holds,
                   f"next call intact {still}; validation still refuses "
                   f"{holds}"))


def main() -> int:
    checks: list = []
    reads_once(checks)
    bus_costs(checks, random.randint(150, 229))
    put_cost(checks)
    unchanged(checks)
    return report("write validation cost", checks,
                  "a validated write costs the validation, within its budget")


if __name__ == "__main__":
    sys.exit(main())
