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
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .idlmodel import PRIMITIVES, Member, Model

#: The vendored generated artifacts, which travel inside the distribution:
#: the serializer tables, the JSON Schemas, and the pinned IDL. There is one
#: copy and both halves of the package read it -- the client's lift needs the
#: tables, the server needs the tables, the schemas and the IDL -- because two
#: copies of a generated file is two answers waiting to happen.
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"

_STR_INTS = {"int64", "uint64"}
_TABLES: dict | None = None


# Appendix N.1's canonical form lives in `spatialdds.canonjson`, a leaf
# shared with the client half: both sides have to produce byte-identical
# canonical JSON -- this one for ETags and for canonicalizing a published
# record, the other for a `checksum` a reader can recompute -- and two
# implementations of a byte-exact format is two answers waiting to happen.
# Re-exported here because every call site in this half says `webjson.canon`.
from ..canonjson import canon, digest, ecma262

#: Re-exported from the shared leaf. Named here so that "this module still
#: answers to `webjson.canon`" is a statement rather than an accident.
__all__ = ["canon", "digest", "ecma262", "ARTIFACTS", "Lifter", "Projector",
           "OCTET_RUN", "tables", "schemas", "schema_file_for",
           "schema_from_model", "served_schemas"]


@lru_cache(maxsize=1)
def tables() -> dict:
    """The vendored serializer tables, read once.

    The serving path and the publish path must project through the same
    tables; loading the file in two places invites two answers.
    """
    global _TABLES
    if _TABLES is None:
        _TABLES = json.loads((ARTIFACTS / "serializer_tables.json").read_text())
    return _TABLES


def schemas() -> dict[str, dict]:
    """Every vendored JSON Schema, keyed by its `title` (the dotted FQN).

    Keyed the way the specification repository's conformance runner keys them,
    so "the schema for this type" means the same thing on both sides.
    """
    out: dict[str, dict] = {}
    for p in sorted((ARTIFACTS / "schemas").glob("*.json")):
        doc = json.loads(p.read_text())
        out[doc.get("title") or p.stem] = doc
    return out


def schema_file_for(fqn: str) -> str:
    """`spatial.core.GeoAnchor` -> `spatial_core_GeoAnchor`."""
    return fqn.replace(".", "_")


# -- a schema for a type the vendored set does not have -----------------------
# Appendix N.2: "An endpoint MUST publish a schema for every type it serves."
# The vendored schemas cover the pin; a deployment's own type (a §3.3.2
# extension, registered with `sd.dds.extend()` or carried in a recording's
# IDL) was served with none until 0.7.4 (S19). So the gateway writes one from
# the same model it decodes the type with, by the rules the specification's
# generator (web-binding/gen.py) uses. gates/schema_from_model.py holds the
# two to each other: from the pin's model this reproduces every vendored
# schema exactly, so a schema it writes for an extension is the one the
# specification's generator would have written.

_INTEGER = {"int8": (-128, 127), "uint8": (0, 255), "int16": (-32768, 32767),
            "uint16": (0, 65535), "int32": (-2147483648, 2147483647),
            "uint32": (0, 4294967295)}
_BASE64 = {"type": "string", "contentEncoding": "base64"}


def _primitive(name: str) -> dict | None:
    wire = PRIMITIVES[name][1] if name in PRIMITIVES else name
    if wire == "bool":
        return {"type": "boolean"}
    if wire in _INTEGER:
        lo, hi = _INTEGER[wire]
        return {"type": "integer", "minimum": lo, "maximum": hi}
    if wire == "int64":
        return {"type": "string", "pattern": r"^-?\d+$"}
    if wire == "uint64":
        return {"type": "string", "pattern": r"^\d+$"}
    if wire in ("float32", "float64"):
        return {"type": "number"}
    if wire == "string":
        return {"type": "string"}
    return None


class _SchemaWriter:
    def __init__(self, model: Model) -> None:
        self.model = model
        self.defs: dict[str, dict] = {}
        self.seen: set[str] = set()

    def _octets(self, t: str) -> bool:
        return self.model.resolve(t) in OCTET_RUN

    def member(self, m: Member) -> dict:
        if m.sequence:
            s = _BASE64 if self._octets(m.type) else {
                "type": "array", "items": self.element(m.type)}
            bound = self.model.bound(m)
            if s is not _BASE64 and bound is not None:
                s["maxItems"] = bound
        else:
            s = self.element(m.type)
        for n in reversed(m.array):
            s = _BASE64 if (not m.sequence and self._octets(m.type)) else {
                "type": "array", "items": s, "maxItems": n, "minItems": n}
        return dict(s)

    def element(self, name: str) -> dict:
        t = self.model.lookup(name)
        if t in self.model.typedefs:
            return self.member(self.model.typedefs[t])
        p = _primitive(t)
        if p is not None:
            return p
        agg = self.model.aggregates.get(t)
        if agg is None:
            raise KeyError(f"the model has no type {name!r}")
        if agg.kind == "enum":
            return {"type": "string", "enum": list(agg.enumerators)}
        self.define(t)
        return {"$ref": f"#/$defs/{schema_file_for(t)}"}

    def define(self, fqn: str) -> None:
        if fqn in self.seen:
            return
        self.seen.add(fqn)
        agg = self.model.aggregates[fqn]
        if agg.kind == "struct":
            self.defs[schema_file_for(fqn)] = {
                "type": "object",
                "properties": {m.name: self.member(m) for m in agg.members},
                "required": [m.name for m in agg.members],
                "additionalProperties": True}
            return
        branches = []
        for labels, m in agg.cases:
            b = {"type": "object", "required": ["type"],
                 "properties": {"type": {"const": labels[0].split(".")[-1]}},
                 "additionalProperties": True}
            if m.name != "none":
                b["properties"][m.name] = self.member(m)
            branches.append(b)
        self.defs[schema_file_for(fqn)] = {"oneOf": branches}


def schema_from_model(model: Model, fqn: str) -> dict:
    """The JSON Schema for one struct, written from the wire model."""
    w = _SchemaWriter(model)
    w.define(fqn)
    doc = {"$schema": "https://json-schema.org/draft/2020-12/schema",
           "$id": f"spatialdds:webjson:{fqn}", "title": fqn}
    doc.update(w.defs.pop(schema_file_for(fqn)))
    if w.defs:
        doc["$defs"] = dict(sorted(w.defs.items()))
    return doc


def served_schemas(model: Model | None, served: Any) -> dict[str, dict]:
    """The vendored schemas, plus one written for each served type without one.

    `served` is the dotted FQNs the endpoint serves. A served type the model
    cannot describe is left out, and so stays visible as a gap to anything
    that checks the index against what is served.
    """
    out = dict(schemas())
    if model is None:
        return out
    for fqn in sorted(set(served) - set(out)):
        agg = model.aggregates.get(fqn)
        if agg is not None and agg.kind == "struct":
            out[fqn] = schema_from_model(model, fqn)
    return out


# The IDL spellings of an octet run. N.1 makes such a run base64, and the
# generated serializer tables say `"t": "base64"` for all three of them -- but
# the specification's IDL spells them `sequence<uint8, N>` and never `octet`,
# so a rule written against `octet` alone matched nothing. It went unnoticed
# because neither recording carries a message with an octet run: finding 33.
# gates/wire_model.py now checks the two readings against each other on this
# rule specifically, so a fourth spelling cannot arrive unexercised.
OCTET_RUN = ("octet", "uint8")


class Lifter:
    """The inverse of `Projector`: binding JSON back to wire values.

    A reader never needs this; a publisher does, and so this is the half of the
    mapping a client library will need to send anything. It exists because the
    publish path canonicalizes by round trip -- JSON in, XCDR2 out, decoded and
    projected back -- and `Writer` encodes decoded values rather than canonical
    JSON. Only three rules actually differ between the two representations, and
    each is inverted here in the same order `Projector` applies it:

    * `int64`/`uint64` are unconditional decimal **strings** in JSON (N.1) and
      integers on the wire.
    * An octet run is base64 in JSON and a byte sequence on the wire. `Writer`
      flattens arrays itself, so the decoded form is a flat list of integers.
    * A union is tagged on `"type"` in JSON and carries `_discriminator`
      through the decoder. A branch the JSON suppresses still occupies the
      wire, and `Writer` writes zero for it.

    Everything else -- enumerator names, floats, bools, strings, nesting --
    is already in the form the writer wants, which is why this is short.
    """

    def __init__(self, model: Model,
                 tables: dict[str, Any] | None = None) -> None:
        self.m = model
        self.tables = tables or {}

    def lift(self, fqn: str, value: Any) -> Any:
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
        label = value.get("type")
        out: dict[str, Any] = {"_discriminator": label}
        for labels, mem in agg.cases:
            if label in labels and mem.name and mem.name in value:
                out[mem.name] = self._member(mem, value[mem.name])
                break
        return out

    def _member(self, mem: Member, v: Any) -> Any:
        base = self.m.resolve(mem.type)
        if base in OCTET_RUN and (mem.sequence or mem.array):
            return list(base64.b64decode(v)) if isinstance(v, str) else list(v)
        if mem.sequence:
            return [self._scalar(mem.type, x) for x in v]
        if mem.array:
            return self._nested(mem.type, v)
        return self._scalar(mem.type, v)

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
                return int(v)               # the decimal string, undone
            if kind in ("float32", "float64"):
                return float(v)
            if kind == "bool":
                return bool(v)
            if kind == "char":
                return v
            return int(v)
        td = self.m.typedefs.get(t)
        if td is not None and (td.array or td.sequence):
            return self._member(Member(td.name, td.type, list(td.array),
                                       td.sequence, td.seq_bound), v)
        agg = self.m.get(t)
        if agg is None:
            raise KeyError(f"no wire model for {type_name!r}")
        if agg.kind == "enum":
            return v                        # the enumerator name, unchanged
        if agg.kind == "union":
            return self._union(agg, v)
        return self.lift(t, v)


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
        if base in OCTET_RUN and (mem.sequence or mem.array):
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
