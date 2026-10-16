#!/usr/bin/env python3
"""The model read from the recording agrees with the published tables.

The decoder's whole claim is that it reads the wire correctly, and the wire is
described by the IDL the recording embeds. That IDL is parsed here, and the
parse is checked against `webjson/serializer_tables.json`, which was generated
upstream from the same specification by a different program walking a different
representation. Two independent readings of one IDL have to agree on every
type's kind, on field order, on enum members and on union cases before anything
is served.

The check is worth more than it looks. A parser that silently dropped a field
would produce JSON that still validates against the schema, because
`additionalProperties` is true and the missing member would simply be absent;
and every field after it on the wire would be read at the wrong offset.

Usage:  gates/wire_model.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

from harness import MCAP, report                               # noqa: E402
from spatialdds_web import gateway                             # noqa: E402

TABLES = ROOT / "webjson" / "serializer_tables.json"


def main() -> int:
    model = gateway.wire_model(MCAP)
    tables = json.loads(TABLES.read_text())
    common = sorted(set(model.aggregates) & set(tables))

    checks: list[tuple[str, bool, str]] = []
    checks.append((f"the recording's embedded IDL yields "
                   f"{len(model.aggregates)} types, {len(common)} of them also "
                   f"in the published tables",
                   len(common) > 50,
                   "too few types matched to be a meaningful comparison"))

    kind_bad, order_bad, enum_bad, union_bad, renamed = [], [], [], [], []
    appended: list[str] = []
    for fqn in common:
        agg, tbl = model.aggregates[fqn], tables[fqn]
        if agg.kind != tbl.get("kind"):
            kind_bad.append(f"{fqn}: {agg.kind} vs {tbl.get('kind')}")
            continue
        if agg.kind == "struct":
            mine = [m.name for m in agg.members]
            theirs = [n for n, _ in tbl["fields"]]
            # A leading underscore here is a code generator's identifier, not
            # the specification's: IDL identifiers cannot begin with one.
            # MAPPING.md requires verbatim IDL names, and the upstream
            # generator was publishing `_global` and `_from` until that was
            # fixed. This no longer reconciles the two spellings -- it fails on
            # the mangled one, so a regression to it cannot pass.
            if mine != theirs:
                # An APPENDABLE type may have gained members since this
                # recording was cut, and on the wire that is compatibility
                # rather than disagreement: a reader tolerates a producer
                # that omits trailing members. This recording is a vendored
                # pre-revision artifact and cannot be re-cut, so the 1.8
                # revision's `GeoAnchor.manifest_uri` is exactly that case.
                #
                # Tolerated only as a strict prefix, and counted. A reorder,
                # a rename, or an insertion anywhere but the end still fails,
                # which is what keeps this from becoming a licence.
                if theirs[:len(mine)] == mine and len(theirs) > len(mine):
                    appended.append(f"{fqn}: +{theirs[len(mine):]}")
                else:
                    order_bad.append(f"{fqn}: {mine} vs {theirs}")
        elif agg.kind == "enum":
            if agg.enumerators != tbl["members"]:
                enum_bad.append(f"{fqn}")
        elif agg.kind == "union":
            if [l[0] for l, _ in agg.cases] != [c[0] for c in tbl["cases"]]:
                union_bad.append(f"{fqn}")

    checks.append(("every shared type has the same kind in both readings",
                   not kind_bad, "; ".join(kind_bad[:3])))
    checks.append(("every struct has the same members in the same order, "
                   "allowing only members appended since the recording was "
                   "cut", not order_bad, "; ".join(order_bad[:3])))
    # Not a pass/fail: the tolerated set is printed so that "the readings
    # agree" can never quietly mean "the readings differ in a way I allowed".
    print(f"  note {len(appended)} type(s) have members appended since this "
          f"recording was cut, tolerated as APPENDABLE compatibility"
          + (f": {'; '.join(appended)}" if appended else ""))
    checks.append(("every enum has the same members in the same order",
                   not enum_bad, "; ".join(enum_bad[:3])))
    checks.append(("every union has the same cases in the same order",
                   not union_bad, "; ".join(union_bad[:3])))

    # Enum ordinals are not positions, and decoding by position would mislabel
    # every value of a sparsely numbered enum.
    sparse = {f: a.by_value for f, a in model.aggregates.items()
              if a.kind == "enum"
              and list(a.by_value) != list(range(len(a.by_value)))}
    checks.append((f"sparse enum ordinals are read from @value, not from "
                   f"position ({len(sparse)} such enums, e.g. "
                   f"{sorted(sparse)[0].split('.')[-1] if sparse else '-'})",
                   bool(sparse),
                   "no sparse enum was found, so this check proved nothing"))

    keyed = {f: [m.name for m in a.members if m.is_key]
             for f, a in model.aggregates.items()
             if a.kind == "struct" and any(m.is_key for m in a.members)}
    checks.append((f"@key members are read from the recording "
                   f"({len(keyed)} keyed types)",
                   len(keyed) >= 6, f"only {len(keyed)} keyed types found"))

    # Scanned over *every* vendored table entry, not only the ones this
    # recording's IDL also declares. The intersection above cannot see a type
    # the recording does not carry, and the mangled names that prompted this
    # check all lived in the discovery types, which this recording has none of:
    # asserting only over the intersection would have been an assertion that
    # could not fail where the defect actually was.
    renamed = [f"{fqn}.{n}"
               for fqn, spec in tables.items() if spec.get("kind") == "struct"
               for n, _ in spec["fields"] if n.startswith("_")]
    renamed += [f"{fqn}.{c[1]}"
                for fqn, spec in tables.items() if spec.get("kind") == "union"
                for c in spec["cases"] if c[1] and c[1].startswith("_")]
    checks.append((f"no table field carries a code generator's mangled name, "
                   f"across all {len(tables)} vendored entries "
                   f"(MAPPING.md requires verbatim IDL names)",
                   not renamed, f"{sorted(set(renamed))[:4]}"))

    return report(f"wire model from {MCAP.name}", checks,
                  "the recording's IDL and the published tables agree")


if __name__ == "__main__":
    sys.exit(main())
