"""Decoded samples to canonical web JSON, per the binding's rule table.

`webjson/MAPPING.md` is the ratified rule table and this implements it. The
rules that bite, all from there:

  * `int64`/`uint64` are **decimal strings**, unconditionally, so `Time.sec` is
    a string. The binding's one deliberate divergence from DDS-JSON: a
    JavaScript consumer coerces any JSON number to a double before application
    code sees it, so a value-dependent rule would make a field's type depend on
    its value.
  * `has_*` guards are **literal**. The guard is a boolean and the member it
    qualifies is always present, because that is what the wire carries.
  * Enums are the enumerator **name**, never the ordinal.
  * Unions are tagged objects, `{"type": "<ENUMERATOR>", "<member>": value}`,
    an empty branch being `{"type": "<ENUMERATOR>"}` alone.
  * An `octet` sequence or array is **base64**, not an array of numbers.

Structure comes from the wire model, which came from the recording. The
published `serializer_tables.json` is not used to drive this; it is used to
check it, in `gates/wire_model.py`, so two independent readings of the same IDL
have to agree before anything is served.

Canonical form (`canon`) is for digests only. Wire JSON is ordinary JSON;
`ETag` values are digests of the canonical form, which is why the two live
together.
"""
from __future__ import annotations

import base64
import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from spatialdds_web.idlmodel import PRIMITIVES, Member, Model

ROOT = Path(__file__).resolve().parent.parent
WEBJSON = ROOT / "webjson"

_STR_INTS = {"int64", "uint64"}


def ecma262(x: float) -> str:
    """ECMA-262 Number::toString, which MAPPING.md pins as the digest form.

    "Shortest round-trip" names a property, not a unique string, so the rule
    has to be implemented rather than approximated by whatever `repr` gives: a
    Python producer and a browser consumer have to agree byte for byte or an
    ETag computed on one side will not match the other.
    """
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("non-finite double is not representable "
                         "(NaN deprecated, §2.3)")
    if x == 0:
        return "0"                      # covers -0.0, which ECMA-262 shows "0"
    sign = "-" if x < 0 else ""
    _, digits, exp = Decimal(repr(abs(x))).as_tuple()
    s = "".join(map(str, digits))
    while len(s) > 1 and s.endswith("0"):
        s = s[:-1]
        exp += 1
    k = len(s)
    n = exp + k
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + s
    e = n - 1
    mant = s[0] + ("." + s[1:] if k > 1 else "")
    return sign + mant + "e" + ("+" if e >= 0 else "-") + str(abs(e))


def canon(v: Any) -> str:
    """Canonical JSON text: sorted keys, ECMA-262 numbers, no insignificant ws."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return ecma262(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ",".join(canon(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k) + ":" + canon(v[k])
                              for k in sorted(v)) + "}"
    if v is None:
        return "null"
    raise TypeError(f"uncanonicalizable: {type(v)}")


def digest(v: Any) -> str:
    return hashlib.sha256(canon(v).encode()).hexdigest()


@lru_cache(maxsize=1)
def schemas() -> dict[str, dict]:
    """Every vendored JSON Schema, keyed by its `title` (the dotted FQN).

    Keyed the way the specification repository's conformance runner keys them,
    so "the schema for this type" means the same thing on both sides.
    """
    out: dict[str, dict] = {}
    for p in sorted((WEBJSON / "schemas").glob("*.json")):
        doc = json.loads(p.read_text())
        out[doc.get("title") or p.stem] = doc
    return out


def schema_file_for(fqn: str) -> str:
    """`spatial.core.GeoAnchor` -> `spatial_core_GeoAnchor`."""
    return fqn.replace(".", "_")


class Projector:
    """Projects decoded samples into the binding's JSON.

    Structure comes from the wire model. One thing does not, and cannot: which
    union branches contribute a JSON member. `CovMatrix` declares
    `case COV_NONE: uint8 none;` -- a real byte, present on the wire and read
    by the decoder -- while MAPPING.md says an empty branch is
    `{"type": "COV_NONE"}` with no member at all. That the placeholder is
    padding rather than information is a mapping decision recorded in the
    generated `serializer_tables.json`, where the branch's type is `null`, and
    it is not recoverable from the IDL by any rule a second implementation
    could infer. So the tables are consulted for union shape, and only for
    that. Written up as a finding.
    """

    def __init__(self, model: Model,
                 tables: dict[str, Any] | None = None) -> None:
        self.m = model
        self.tables = tables or {}

    def _suppressed(self, fqn: str, label: str) -> bool:
        """Does this union branch carry no JSON member, per the tables?"""
        tbl = self.tables.get(fqn)
        if not tbl or tbl.get("kind") != "union":
            return False
        for case_label, _member, desc in tbl.get("cases", []):
            if case_label == label:
                return desc is None
        return False

    def project(self, fqn: str, value: Any) -> Any:
        agg = self.m.get(fqn)
        if agg is None:
            raise KeyError(f"no wire model for {fqn}")
        if agg.kind == "union":
            return self._union(agg, value)
        out = {}
        for mem in agg.members:
            if mem.name not in value:
                continue
            out[mem.name] = self._member(mem, value[mem.name])
        return out

    def _union(self, agg, value: dict) -> dict:
        label = value.get("_discriminator")
        out = {"type": label}
        if self._suppressed(agg.name, label):
            return out
        for labels, mem in agg.cases:
            if label in labels and mem.name and mem.name in value:
                out[mem.name] = self._member(mem, value[mem.name])
                break
        return out

    def _member(self, mem: Member, v: Any) -> Any:
        base = self.m.resolve(mem.type)
        # An octet run is base64, which overrides the per-octet number rule.
        if base == "octet" and (mem.sequence or mem.array):
            raw = bytes(bytearray(self._flatten_ints(v)))
            return base64.b64encode(raw).decode()
        if mem.sequence:
            return [self._scalar(mem.type, x) for x in v]
        if mem.array:
            return self._nested(mem.type, v)
        return self._scalar(mem.type, v)

    def _flatten_ints(self, v: Any) -> list[int]:
        out: list[int] = []
        for x in v:
            if isinstance(x, list):
                out.extend(self._flatten_ints(x))
            else:
                out.append(int(x) & 0xFF)
        return out

    def _nested(self, type_name: str, v: Any) -> Any:
        if isinstance(v, list):
            return [self._nested(type_name, x) for x in v]
        return self._scalar(type_name, v)

    def _scalar(self, type_name: str, v: Any) -> Any:
        t = self.m.resolve(type_name)
        if t == "string":
            return v
        if t in PRIMITIVES:
            kind = PRIMITIVES[t][1]
            if kind in _STR_INTS:
                return str(int(v))          # unconditional decimal string
            if kind in ("float32", "float64"):
                return float(v)
            if kind == "bool":
                return bool(v)
            if kind == "char":
                return v.decode() if isinstance(v, bytes) else str(v)
            return int(v)
        td = self.m.typedefs.get(t)
        if td is not None and (td.array or td.sequence):
            return self._member(Member(td.name, td.type, list(td.array),
                                       td.sequence, td.seq_bound), v)
        agg = self.m.get(t)
        if agg is None:
            raise KeyError(f"no wire model for {type_name!r}")
        if agg.kind == "enum":
            return v                        # already the enumerator name
        if agg.kind == "union":
            return self._union(agg, v)
        return self.project(t, v)
