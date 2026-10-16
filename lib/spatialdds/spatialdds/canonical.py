"""The lift: Appendix N.1 canonical JSON back to wire values.

This is the library's reason to exist. Three rules separate the JSON an
endpoint serves from the values a program wants, and all three are easy to get
subtly wrong:

* `int64` and `uint64` are **unconditional decimal strings** in JSON and
  integers on the wire. A client that treats `stamp.sec` as a string gets
  surprising comparisons; one that calls `int()` on every string corrupts
  identifiers.
* An octet run is **base64** in JSON and bytes on the wire.
* A union is tagged on `"type"` in JSON and carries `_discriminator` through a
  decoder.

Everything else -- enumerator names, floats, bools, nesting -- is already in
the form a program wants, which is why this module is short.

**These are not this library's rules.** They are
`spatialdds.server.webjson`'s, and `gates/library.py` proves the two agree by
lifting every record in both committed recordings with each and comparing. The
server's `Lifter` is driven by an IDL model; a client reading a remote endpoint
has no IDL, so this one is driven by the generated serializer tables, which
carry the same facts per field. Same rules, same results, different source of
the type information -- and the equality is gated, not asserted. It earned its
keep on 2026-10-15, when the disagreement turned out to be in the server
(finding 33).

The two halves read **one** copy of the tables, under `artifacts/`, which is
also what the server reads. Importing this module pulls in nothing but the
standard library: `spatialdds.server` is not imported here and must not be.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional

_TABLES: Optional[Dict[str, Any]] = None
_STR_INTS = ("int64", "uint64")


def tables() -> Dict[str, Any]:
    """The vendored serializer tables, read once."""
    global _TABLES
    if _TABLES is None:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "artifacts",
                               "serializer_tables.json")) as fh:
            _TABLES = json.load(fh)
    return _TABLES


def lift(fqn: str, value: Any) -> Any:
    """Canonical JSON for `fqn` as wire values. The inverse of the projection.

    Unknown types pass through untouched rather than raising: an endpoint may
    serve a type newer than these tables, and a client that refuses to read it
    is worse than one that hands back what it was given.
    """
    entry = tables().get(fqn)
    if entry is None or not isinstance(value, dict):
        return value
    if entry.get("kind") == "union":
        return _union(entry, value)
    if entry.get("kind") != "struct":
        return value
    out: Dict[str, Any] = {}
    for name, desc in entry["fields"]:
        if name in value:
            out[name] = _member(desc, value[name])
    for k, v in value.items():                 # members the tables do not know
        out.setdefault(k, v)
    return out


def _union(entry: Dict[str, Any], value: Dict[str, Any]) -> Dict[str, Any]:
    label = value.get("type")
    out: Dict[str, Any] = {"_discriminator": label}
    for case_label, member, desc in entry.get("cases", []):
        if case_label == label and member and member in value:
            out[member] = _member(desc, value[member]) if desc else value[member]
            break
    return out


def _member(desc: Optional[Dict[str, Any]], v: Any) -> Any:
    if desc is None:
        return v
    t = desc.get("t")
    if t == "base64":
        return list(base64.b64decode(v)) if isinstance(v, str) else list(v)
    if t == "array":
        return _nested(desc.get("item"), v)
    return _scalar(desc, v)


def _nested(item: Optional[Dict[str, Any]], v: Any) -> Any:
    if isinstance(v, list):
        return [_nested(item, x) for x in v]
    return _member(item, v)


def _scalar(desc: Dict[str, Any], v: Any) -> Any:
    t = desc.get("t")
    if t in _STR_INTS:
        return int(v)                          # the decimal string, undone
    if t == "int":
        return int(v)
    if t == "double":
        return float(v)
    if t == "bool":
        return bool(v)
    if t in ("str", "enum"):
        return v                               # enumerator names pass through
    if t in ("ref", "union"):
        return lift(desc.get("fqn", ""), v)
    return v


def types() -> List[str]:
    """Every type these tables describe."""
    return sorted(tables())
