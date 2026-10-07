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
# idlc renames IDL members that collide with Python keywords, and the upstream
# generator reflects over its output, so the published tables carry `_global`
# and `_from` where the IDL says `global` and `from`. MAPPING.md requires the
# verbatim IDL name, so this parse is right and the tables are not; the pair is
# reconciled here and reported as a finding rather than silently accepted.
PY_KEYWORD_RENAMES = {"_global": "global", "_from": "from"}


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
    for fqn in common:
        agg, tbl = model.aggregates[fqn], tables[fqn]
        if agg.kind != tbl.get("kind"):
            kind_bad.append(f"{fqn}: {agg.kind} vs {tbl.get('kind')}")
            continue
        if agg.kind == "struct":
            mine = [m.name for m in agg.members]
            theirs = []
            for n, _ in tbl["fields"]:
                if n in PY_KEYWORD_RENAMES:
                    renamed.append(f"{fqn}.{n}")
                    n = PY_KEYWORD_RENAMES[n]
                theirs.append(n)
            if mine != theirs:
                order_bad.append(f"{fqn}: {mine[:4]} vs {theirs[:4]}")
        elif agg.kind == "enum":
            if agg.enumerators != tbl["members"]:
                enum_bad.append(f"{fqn}")
        elif agg.kind == "union":
            if [l[0] for l, _ in agg.cases] != [c[0] for c in tbl["cases"]]:
                union_bad.append(f"{fqn}")

    checks.append(("every shared type has the same kind in both readings",
                   not kind_bad, "; ".join(kind_bad[:3])))
    checks.append(("every struct has the same members in the same order",
                   not order_bad, "; ".join(order_bad[:3])))
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

    if renamed:
        print(f"  note the published tables carry {len(set(renamed))} "
              f"Python-mangled member name(s) where the IDL has the plain "
              f"name: {sorted(set(renamed))[:4]}")
        print("       reconciled here; see WEB-BINDING-FINDINGS.md\n")

    return report(f"wire model from {MCAP.name}", checks,
                  "the recording's IDL and the published tables agree")


if __name__ == "__main__":
    sys.exit(main())
