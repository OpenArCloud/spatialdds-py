#!/usr/bin/env python3
"""Every message in the recording decodes, and the counts are the recording's.

The expected numbers come from the recording's own `summary.json`, written when
it was cut: per-topic counts, the total, and the content digest. Not from this
gateway, and not from the adapter that produced the file.

Three claims, which fail separately. Every message decodes, so no sample is
quietly skipped. Every per-topic count matches, so nothing is dropped or
duplicated on one lane while the total still adds up. Every decoded sample
validates against its published JSON Schema, which is what makes "decoded"
mean "decoded correctly" rather than "decoded without raising": a misread
offset produces wrong types, enum names that are not enumerators, and int64
strings that are not digits, and the schema rejects all three.

Usage:  gates/decode_fidelity.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import jsonschema                                              # noqa: E402

from harness import MCAP, report, state, summary                # noqa: E402


def schemas() -> dict[str, dict]:
    out = {}
    for p in sorted((ROOT / "webjson" / "schemas").glob("*.json")):
        doc = json.loads(p.read_text())
        out[doc.get("title") or p.stem] = doc
    return out


def main() -> int:
    summ = summary()
    st = state()
    sch = schemas()
    checks: list[tuple[str, bool, str]] = []

    # The file digest is checked against the pin in webjson/PROVENANCE, not
    # against the summary's `content_sha256`. Those are different things: the
    # summary's is a digest over channels and messages with the writer's
    # publish_time excluded, which is the only part of an MCAP that is
    # reproducible across runs, and it cannot be recomputed here without the
    # producer's algorithm. The file digest is what pins these bytes.
    digest = hashlib.sha256(MCAP.read_bytes()).hexdigest()
    pin = ""
    for line in (ROOT / "webjson" / "PROVENANCE").read_text().splitlines():
        if line.startswith("recording.sha256"):
            pin = line.split("=", 1)[1].strip()
    checks.append(("the recording is the bytes webjson/PROVENANCE pins",
                   pin == digest, f"file {digest[:16]}…, pinned {pin[:16]}…"))

    served = {}
    for res in st.resources.values():
        n = len(st.latched_initial.get(res.topic, {}))
        n += sum(1 for e in st.live.get(res.topic, [])
                 if e["event"] == "sample")
        served[res.topic] = n
    want = summ["counts"]
    checks.append((f"the same {len(want)} topics the recording declares",
                   sorted(served) == sorted(want),
                   f"only here: {sorted(set(served) - set(want))[:2]}; "
                   f"only in summary: {sorted(set(want) - set(served))[:2]}"))
    bad = [f"{t}: {served.get(t)} vs {want[t]}" for t in sorted(want)
           if served.get(t) != want[t]]
    checks.append((f"every per-topic sample count matches the recording "
                   f"({sum(want.values())} samples)",
                   not bad, "; ".join(bad[:3])))
    checks.append(("the total matches the recording's own total",
                   sum(served.values()) == summ["messages"],
                   f"{sum(served.values())} vs {summ['messages']}"))

    n_valid = 0
    invalid: list[str] = []
    for res in st.resources.values():
        schema = sch.get(res.fqn)
        if schema is None:
            invalid.append(f"{res.fqn}: no published schema")
            continue
        seen = list(st.latched_initial.get(res.topic, {}).values())
        docs = [i.data for i in seen]
        docs += [e["data"] for e in st.live.get(res.topic, [])
                 if e["event"] == "sample"]
        for d in docs:
            try:
                jsonschema.validate(d, schema)
                n_valid += 1
            except jsonschema.ValidationError as e:
                if len(invalid) < 4:
                    invalid.append(f"{res.fqn}: {e.message[:70]}")
    checks.append((f"all {n_valid} decoded sample(s) validate against their "
                   f"published schema",
                   not invalid and n_valid == summ["messages"],
                   "; ".join(invalid[:3]) or
                   f"validated {n_valid} of {summ['messages']}"))

    return report(f"recording {MCAP.name}", checks,
                  "every message decodes and the counts are the recording's")


if __name__ == "__main__":
    sys.exit(main())
