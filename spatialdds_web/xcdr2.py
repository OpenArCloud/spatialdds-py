"""Read XCDR2 messages using a wire model from the recording's own IDL.

Enough of XCDR2 to read what a SpatialDDS producer writes, and no more. The
scope is set by what the specification's types actually use, which was measured
rather than assumed: every aggregate in the 1.8 profiles is APPENDABLE, there
are no MUTABLE aggregates and so no parameter lists, and the only non-struct
aggregates are two unions and a set of enums.

The pieces:

  * **Encapsulation.** Four bytes: a representation identifier and two option
    bytes. `0x0009` is XCDR2 little-endian delimited, which is what these
    recordings carry. Alignment is measured from the end of that header, not
    from the start of the message, which is the usual place to get this wrong.
  * **DHEADER.** Every APPENDABLE aggregate is preceded by a uint32 giving the
    byte length of its contents. The decoder uses it twice: to skip members a
    newer writer appended and this model does not know about, which is the
    whole point of APPENDABLE, and as a bounds check, because a DHEADER that
    disagrees with the members decoded inside it means the model and the wire
    have diverged and every later field would be silently misread.
  * **Alignment, capped at four.** XCDR2 aligns a primitive to the *smaller of
    its size and four bytes*, which is one of the real differences from XCDR1
    and the single most expensive thing to get wrong here. An 8-byte double or
    int64 is 4-aligned, not 8-aligned. Fitted to real messages rather than
    assumed: in one recorded `Detection2DSet` a `double[4]` begins at offset
    160 and in a `FusedTrackSet` a `Vec3` begins at 268, both 4-aligned and
    only one of them 8-aligned, and the three `int64` stamps at 208, 388 and
    440 are likewise 4-aligned and not consistently 8-aligned. Assuming
    natural 8-byte alignment inserts phantom padding, and every field after it
    reads as noise. Strings and
    sequences are length-prefixed uint32. Fixed arrays are not: their bound
    comes from the IDL, which is the distinction the published serializer
    tables erase and the reason this package parses IDL at all.
  * **Enums** are int32 carrying the declared `@value`, which is not the
    member's position: `CovarianceType` runs 0, 3, 6, 9, 12. Decoding by
    position would mislabel every covariance union in the file.

Trailing bytes inside a DHEADER are skipped, not an error: that is forward
compatibility working. Running *out* of bytes is an error, loudly, because it
means the model is wrong rather than old.
"""
from __future__ import annotations

import struct
from typing import Any

from spatialdds_web.idlmodel import PRIMITIVES, Aggregate, Member, Model

# Representation identifiers we accept, from the XTypes encapsulation table.
XCDR2_LE_DELIMITED = 0x0009
XCDR2_BE_DELIMITED = 0x000A
XCDR2_LE_PLAIN = 0x0007
XCDR2_BE_PLAIN = 0x0008

_FMT = {
    "bool": "?", "octet": "B", "char": "c",
    "int8": "b", "uint8": "B",
    "int16": "h", "uint16": "H",
    "int32": "i", "uint32": "I",
    "int64": "q", "uint64": "Q",
    "float32": "f", "float64": "d",
}


class WireError(Exception):
    """The bytes and the model disagree. Never raised for unknown trailing
    members, which are a writer being newer and are skipped."""


class Reader:
    def __init__(self, data: bytes, little: bool, base: int) -> None:
        self.d = data
        self.p = base
        self.base = base            # alignment origin: end of encapsulation
        self.e = "<" if little else ">"

    def align(self, n: int) -> None:
        """Align to min(n, 4): XCDR2 caps primitive alignment at four bytes."""
        n = 4 if n > 4 else n
        off = (self.p - self.base) % n
        if off:
            self.p += n - off

    def prim(self, kind: str) -> Any:
        size, fmt = {
            "bool": (1, "?"), "octet": (1, "B"), "char": (1, "c"),
            "int8": (1, "b"), "uint8": (1, "B"),
            "int16": (2, "h"), "uint16": (2, "H"),
            "int32": (4, "i"), "uint32": (4, "I"),
            "int64": (8, "q"), "uint64": (8, "Q"),
            "float32": (4, "f"), "float64": (8, "d"),
        }[kind]
        self.align(size)
        if self.p + size > len(self.d):
            raise WireError(f"ran out of bytes reading {kind} at {self.p}")
        (v,) = struct.unpack_from(self.e + fmt, self.d, self.p)
        self.p += size
        return v

    def u32(self) -> int:
        return int(self.prim("uint32"))

    def string(self) -> str:
        n = self.u32()
        if self.p + n > len(self.d):
            raise WireError(f"string length {n} overruns the message")
        raw = self.d[self.p:self.p + n]
        self.p += n
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")

    def bytes_n(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise WireError("octet run overruns the message")
        raw = self.d[self.p:self.p + n]
        self.p += n
        return raw


class Decoder:
    """Decodes one type at a time, driven by a `Model`."""

    def __init__(self, model: Model) -> None:
        self.m = model

    # -- entry point --------------------------------------------------------
    def decode(self, fqn: str, data: bytes) -> dict:
        if len(data) < 4:
            raise WireError("message shorter than its encapsulation header")
        rep = struct.unpack_from(">H", data, 0)[0]
        if rep in (XCDR2_LE_DELIMITED, XCDR2_LE_PLAIN):
            little = True
        elif rep in (XCDR2_BE_DELIMITED, XCDR2_BE_PLAIN):
            little = False
        else:
            raise WireError(f"unsupported data representation 0x{rep:04x}; "
                            f"this reads XCDR2, plain or delimited")
        r = Reader(data, little, 4)
        agg = self.m.get(fqn)
        if agg is None:
            raise WireError(f"{fqn} is not in the wire model built from the "
                            f"recording's embedded IDL")
        return self._aggregate(agg, r)

    # -- aggregates ---------------------------------------------------------
    def _aggregate(self, agg: Aggregate, r: Reader) -> Any:
        delimited = agg.extensibility in ("APPENDABLE", "MUTABLE")
        end = None
        if delimited:
            n = r.u32()
            end = r.p + n
            if end > len(r.d):
                raise WireError(
                    f"{agg.name}: DHEADER says {n} bytes, only "
                    f"{len(r.d) - r.p} remain")
        if agg.kind == "union":
            out = self._union(agg, r, end)
        else:
            out = {}
            for mem in agg.members:
                if end is not None and r.p >= end:
                    # A writer older than this model, or a model that lists
                    # members this writer never wrote. Either way the DHEADER
                    # is authoritative and the rest is absent.
                    break
                out[mem.name] = self._member(mem, r)
        if end is not None:
            over = r.p - end
            if over > 0:
                raise WireError(
                    f"{agg.name}: read {over} bytes past its DHEADER, so the "
                    f"model does not match the wire")
            r.p = end               # skip members a newer writer appended
        return out

    def _union(self, agg: Aggregate, r: Reader, end: int | None) -> dict:
        disc_t = self.m.resolve(agg.disc_type)
        disc_agg = self.m.get(disc_t)
        if disc_agg is not None and disc_agg.kind == "enum":
            raw = int(r.prim("int32"))
            label = disc_agg.by_value.get(raw)
            if label is None:
                raise WireError(f"{agg.name}: discriminator {raw} is not a "
                                f"declared enumerator of {disc_t}")
        else:
            raw = r.prim(PRIMITIVES.get(disc_t, (4, "int32"))[1])
            label = str(raw)
        chosen: Member | None = None
        for labels, mem in agg.cases:
            if label in labels:
                chosen = mem
                break
        if chosen is None:
            for labels, mem in agg.cases:
                if "default" in labels:
                    chosen = mem
                    break
        out = {"_discriminator": label}
        if chosen is not None and chosen.type != "void" and chosen.name:
            out[chosen.name] = self._member(chosen, r)
        return out

    # -- members ------------------------------------------------------------
    def _elem_primitive(self, type_name: str) -> bool:
        """Is this collection's element type primitive, for the DHEADER rule?

        XCDR2 delimits a collection whose element type is **not** primitive, so
        the byte length of the whole collection precedes its element count.
        Miss it and the length is read as the count, which is how a two-element
        sequence becomes a 361-element one and every field after it is noise.
        Measured on real messages rather than assumed: a `sequence<Detection2D>`
        carries `DHEADER, count, DHEADER, element…`, and a `double[3]` carries
        its three doubles with nothing in front.
        """
        t = self.m.resolve(type_name)
        if t in PRIMITIVES:
            return True
        td = self.m.typedefs.get(t)
        if td is not None and not td.array and not td.sequence:
            return self._elem_primitive(td.type)
        return False

    def _member(self, mem: Member, r: Reader) -> Any:
        if mem.sequence:
            if not self._elem_primitive(mem.type):
                r.u32()                 # collection DHEADER
            n = r.u32()
            return [self._scalar(mem.type, r) for _ in range(n)]
        if mem.array:
            total = 1
            for b in mem.array:
                total *= b
            if not self._elem_primitive(mem.type):
                r.u32()                 # delimited collection
            # An octet array is a byte run, matching the base64 JSON rule.
            if self.m.resolve(mem.type) == "octet":
                flat: Any = list(r.bytes_n(total))
            else:
                flat = [self._scalar(mem.type, r) for _ in range(total)]
            for b in reversed(mem.array[1:]):
                flat = [flat[i:i + b] for i in range(0, len(flat), b)]
            return flat
        return self._scalar(mem.type, r)

    def _scalar(self, type_name: str, r: Reader) -> Any:
        t = self.m.resolve(type_name)
        if t == "string":
            return r.string()
        if t in PRIMITIVES:
            return r.prim(PRIMITIVES[t][1])
        td = self.m.typedefs.get(t)
        if td is not None and (td.array or td.sequence):
            return self._member(Member(td.name, td.type, list(td.array),
                                       td.sequence, td.seq_bound), r)
        agg = self.m.get(t)
        if agg is None:
            raise WireError(f"no wire model for type {type_name!r}")
        if agg.kind == "enum":
            raw = int(r.prim("int32"))
            name = agg.by_value.get(raw)
            if name is None:
                raise WireError(f"{t}: value {raw} is not a declared "
                                f"enumerator")
            return name
        return self._aggregate(agg, r)
