#!/usr/bin/env python3
"""The JSON projection reproduces the specification's golden vectors.

`webjson/golden/` holds vectors authored by hand upstream, against MAPPING.md,
deliberately not produced by the generator. If this projection and those
vectors agree, the agreement is evidence; checking against the schemas alone
would be weaker, because the schemas and the serializer tables come from one
generator and agreeing with each other proves only self-consistency.

A vector's `sample` is logical field values in declaration order, which is
exactly the shape this package's decoder produces, so it can be projected
directly. This gate therefore covers the JSON rules -- int64 as a decimal
string, enums by name, unions tagged on `"type"`, literal `has_*` guards -- and
not the CDR reader, which `decode_fidelity` covers over 15,676 real messages.

**The number rule is checked as text, separately.** Comparing parsed values
cannot see it: `0.1` and `0.10000000000000001` are the same double, so the
adversarial-double vector would pass against a serializer that got ECMA-262
entirely wrong. The digest form is text, and text is where the rule bites.

Usage:  gates/golden_json.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
# The gateway lives in the distribution under lib/spatialdds; a gate runs
# from a checkout, so the checkout's copy goes on the path rather than an
# installed one.
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import ARTIFACTS, FOUNTAIN, MCAP, report                     # noqa: E402
from spatialdds.server import gateway, webjson                    # noqa: E402

GOLDEN = ROOT / "webjson" / "golden"
# The pairs MAPPING.md states for the digest form of a number.
ECMA_PAIRS = [(0.1, "0.1"), (0.1 + 0.2, "0.30000000000000004"),
              (1e21, "1e+21"), (1.0, "1"), (-0.0, "0")]


def main() -> int:
    # Both recordings, merged. The golden vectors are the specification's own
    # artifacts and cover types across the whole tree; no single recording
    # here carries every type, and the retail one is a vendored pre-revision
    # artifact whose IDL predates `GeoAnchor.manifest_uri` and `TopicMeta`
    # entirely. Taking the union is what lets a vector be checked against the
    # IDL that actually defines its type, rather than against whichever
    # recording happened to be the default.
    model = gateway.wire_model(MCAP)
    for extra in (FOUNTAIN,):
        if extra.is_file():
            m2 = gateway.wire_model(extra)
            # Later definitions win on conflict, which here means the newer
            # recording's post-revision types replace the older ones.
            model.aggregates.update(m2.aggregates)
            model.typedefs.update(m2.typedefs)
    model._leaves = None
    tables = json.loads((ARTIFACTS
                         / "serializer_tables.json").read_text())
    proj = webjson.Projector(model, tables)
    checks: list[tuple[str, bool, str]] = []
    vectors = sorted(GOLDEN.glob("*.json"))
    checks.append((f"{len(vectors)} golden vector(s) present",
                   len(vectors) >= 4, "vectors missing"))

    for p in vectors:
        vec = json.loads(p.read_text())
        fqn = vec["type"].replace("::", ".")
        if model.get(fqn) is None:
            checks.append((f"{p.stem}: {fqn} is in the recording's IDL",
                           False, "type absent from this recording"))
            continue
        try:
            got = proj.project(fqn, vec["sample"])
        except Exception as e:
            checks.append((f"{p.stem}: projects at all", False,
                           f"{type(e).__name__}: {e}"))
            continue
        want = vec["expected_json"]
        ok = got == want
        detail = ""
        if not ok:
            diff = [k for k in sorted(set(got) | set(want))
                    if got.get(k) != want.get(k)]
            detail = "; ".join(f"{k}: got {got.get(k)!r} want {want.get(k)!r}"
                               for k in diff[:3])
        checks.append((f"{p.stem}: projects to the expected JSON", ok, detail))
        try:
            webjson.canon(got)
            webjson.digest(got)
            cok, cd = True, ""
        except Exception as e:
            cok, cd = False, f"{type(e).__name__}: {e}"
        checks.append((f"{p.stem}: canonicalizes and digests", cok, cd))

    bad = [f"{x!r} -> {webjson.ecma262(x)!r}, MAPPING.md says {w!r}"
           for x, w in ECMA_PAIRS if webjson.ecma262(x) != w]
    checks.append((f"the digest form of a number matches every pair "
                   f"MAPPING.md states ({len(ECMA_PAIRS)})",
                   not bad, "; ".join(bad)))
    return report(f"golden vectors {GOLDEN.name}", checks,
                  "the projection agrees with the authored vectors")


if __name__ == "__main__":
    sys.exit(main())
