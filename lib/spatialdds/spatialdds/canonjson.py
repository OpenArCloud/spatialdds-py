"""Appendix N.1 canonical JSON. One implementation, shared by both halves.

This module belongs to neither the client nor the server. Both need to produce
the exact canonical bytes N.1 specifies -- the server to compute an ETag and to
canonicalize a published record, the client to fill a `checksum` a reader can
recompute -- and two implementations of a byte-exact format is two answers
waiting to happen. `spatialdds.records` digested `json.dumps` output before
this module existed, which renders `1.0` where N.1 renders `1`.

It imports nothing but the standard library, so importing it costs neither
half anything of the other.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any


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
