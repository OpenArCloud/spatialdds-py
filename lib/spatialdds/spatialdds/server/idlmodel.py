"""A wire model read out of a recording's own embedded IDL.

An MCAP written by a SpatialDDS producer carries, per type, the flattened IDL
for that type's whole dependency closure. That text is the only description of
the wire this package needs, and reading it is what lets this repository serve
a recording it has never seen before without vendoring that recording's
schemas.

**Why this file exists at all.** The published `serializer_tables.json` gives
field order and JSON types, and for emitting JSON that is enough. It is not
enough to *decode*: a `double[3]` and a `sequence<string,32>` are both
`{"t":"array"}` in the tables, and on the wire one has a length prefix and the
other does not. The distinction the tables erase is exactly the one CDR needs,
so the layout has to come from the IDL.

**Why a parser rather than `idlc`.** The reference compiler cannot read these
payloads. `idlc -l py` compiles the specification's own `events.idl` without
complaint and segfaults on the flattened schema for the same types, whether or
not the comments are stripped, so a consumer cannot in practice rebuild types
from a recording with the reference toolchain. That is written up as a finding;
this parser is the way around it.

**Scope, deliberately small.** This reads declarations, not semantics: module
nesting, struct and union members, typedefs, enum member order, array bounds,
sequence bounds, and the `@extensibility` annotation. It resolves no
expressions, evaluates no constants and has no opinion about anything the wire
does not need. `gates/wire_model.py` checks every type it produces against the
published serializer tables, so the two independent descriptions of the same
IDL have to agree on field order and on every field's JSON type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Primitive IDL types, with the CDR size and alignment each one takes.
PRIMITIVES: dict[str, tuple[int, str]] = {
    "boolean": (1, "bool"),
    "octet": (1, "octet"),
    "char": (1, "char"),
    "int8": (1, "int8"),
    "uint8": (1, "uint8"),
    "short": (2, "int16"),
    "int16": (2, "int16"),
    "unsigned short": (2, "uint16"),
    "uint16": (2, "uint16"),
    "long": (4, "int32"),
    "int32": (4, "int32"),
    "unsigned long": (4, "uint32"),
    "uint32": (4, "uint32"),
    "long long": (8, "int64"),
    "int64": (8, "int64"),
    "unsigned long long": (8, "uint64"),
    "uint64": (8, "uint64"),
    "float": (4, "float32"),
    "double": (8, "float64"),
}


@dataclass
class Member:
    name: str
    type: str                       # resolved type name, or a primitive
    array: list[int] = field(default_factory=list)   # fixed array bounds
    sequence: bool = False
    seq_bound: int | None = None
    is_key: bool = False
    # A bound spelled as a named constant (`sequence<FusedTrack, SZ_SMALL>`),
    # kept by name. Decoding never needs it; a schema's `maxItems` does, and
    # `Model.bound()` resolves it against the IDL's integer constants.
    bound_name: str | None = None


@dataclass
class Aggregate:
    kind: str                       # "struct" | "union" | "enum"
    name: str                       # fully qualified, dotted
    extensibility: str = "FINAL"
    members: list[Member] = field(default_factory=list)
    enumerators: list[str] = field(default_factory=list)
    # ordinal -> enumerator name. An enum is an int32 on the wire carrying the
    # declared value, and several profiles use non-consecutive `@value(N)`, so
    # position in the declaration is not the wire value and cannot be used as
    # one. The binding serializes the *name*, which is why getting this
    # mapping right is the difference between "OBSERVED" and a wrong label on
    # every sample.
    by_value: dict[int, str] = field(default_factory=dict)
    disc_type: str = ""             # union only
    cases: list[tuple[list[str], Member]] = field(default_factory=list)


_STRIP_COMMENT = re.compile(r"//[^\n]*")
# A sequence bound may be a named constant (`sequence<FusedTrack,
# SZ_SMALL>`), not just a literal. The bound does not affect decoding --
# a sequence is length-prefixed either way -- so a non-numeric bound is
# recorded as absent rather than resolved, but it must not be mistaken
# for part of the element type.
_SEQ = re.compile(r"^sequence\s*<\s*(.+?)\s*(?:,\s*([\w:]+)\s*)?>$")
_TOKEN = re.compile(r"@\w+(?:\([^)]*\))?|[A-Za-z_][\w:]*|\d+|.", re.S)


def _strip(text: str) -> str:
    """Comments out. Nothing else: the parser is whitespace-tolerant."""
    return _STRIP_COMMENT.sub("", text)


class Model:
    """Every aggregate the IDL declares, by fully qualified dotted name."""

    def __init__(self) -> None:
        self.aggregates: dict[str, Aggregate] = {}
        self.typedefs: dict[str, Member] = {}
        #: Integer constants, by fully qualified dotted name. Read only so a
        #: sequence bound given by name can be stated in a schema.
        self.constants: dict[str, int] = {}
        self._leaves: dict[str, list[str]] | None = None

    # -- lookup -------------------------------------------------------------
    def _leaf_index(self) -> dict[str, list[str]]:
        if getattr(self, "_leaves", None) is None:
            idx: dict[str, list[str]] = {}
            for fqn in list(self.aggregates) + list(self.typedefs):
                idx.setdefault(fqn.rsplit(".", 1)[-1], []).append(fqn)
            self._leaves = idx
        return self._leaves

    def lookup(self, name: str) -> str:
        """Resolve a reference as the IDL compiler does, not as IDL says.

        The specification references `ZoneKind` unqualified from inside
        `spatial::events::SpatialZone`, while the enum is declared one level
        *down*, in `spatial::events::enums::zone_kind_enum`. Strict IDL scope
        rules would not find it; `idlc` does, and the published bindings and
        serializer tables are built on that, so a reader that insisted on the
        strict rule would reject the specification's own types.

        So: an exact qualified name wins; otherwise a unique declaration with
        that leaf name is taken. An ambiguous leaf raises rather than guessing,
        because picking the wrong same-named type would desynchronise every
        field after it and the symptom would be an absurd string length a long
        way from the cause.
        """
        if name in self.aggregates or name in self.typedefs:
            return name
        if name in PRIMITIVES or name == "string":
            return name
        # A qualified reference may be *relative* to its enclosing scope:
        # `module events` aliases the nested enum as
        # `typedef enums::zone_kind_enum::ZoneKind ZoneKind;`, so the alias
        # target reads `enums.zone_kind_enum.ZoneKind` and is not an absolute
        # name. Match on a dotted-suffix boundary before falling back to the
        # leaf, because a suffix carries more of the author's intent.
        if "." in name:
            suf = [f for f in list(self.aggregates) + list(self.typedefs)
                   if f.endswith("." + name)]
            if len(suf) == 1:
                return suf[0]
        cands = self._leaf_index().get(name.rsplit(".", 1)[-1], [])
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            # Prefer the candidate sharing the longest dotted prefix with the
            # reference, which is the nearest enclosing scope.
            ref = name.split(".")
            best, score = None, -1
            for c in cands:
                parts = c.split(".")
                k = 0
                while k < min(len(ref), len(parts)) and ref[k] == parts[k]:
                    k += 1
                if k > score:
                    best, score = c, k
                elif k == score:
                    best = None
            if best is not None:
                return best
            raise KeyError(f"{name!r} is ambiguous: {cands}")
        return name

    def resolve(self, name: str) -> str:
        """Follow typedef chains to a concrete aggregate or primitive."""
        name = self.lookup(name)
        seen = set()
        while name in self.typedefs and name not in seen:
            seen.add(name)
            td = self.typedefs[name]
            if td.array or td.sequence:
                return name          # an array/sequence typedef is concrete
            name = self.lookup(td.type)
        return name

    def bound(self, m: Member) -> int | None:
        """A sequence member's bound, a named one resolved, or None if unbounded.

        A name is resolved as `lookup` resolves a type: exact, then a unique
        dotted suffix, then a unique leaf. A name that resolves to nothing
        raises, because a bound the IDL states and a schema omits is the
        schema saying something false.
        """
        if m.bound_name is None:
            return m.seq_bound
        name = _dot(m.bound_name)
        if name in self.constants:
            return self.constants[name]
        cands = [c for c in self.constants
                 if c.endswith("." + name) or c.rsplit(".", 1)[-1] == name]
        if len(cands) == 1:
            return self.constants[cands[0]]
        raise KeyError(f"sequence bound {m.bound_name!r} of {m.name!r} "
                       f"names no single integer constant ({cands or 'none'})")

    def get(self, name: str) -> Aggregate | None:
        a = self.aggregates.get(name)
        if a is not None:
            return a
        try:
            return self.aggregates.get(self.lookup(name))
        except KeyError:
            return None


def parse(text: str) -> Model:
    """Parse flattened IDL into a wire model.

    One pass, brace-tracked. Modules push a scope, aggregates collect members
    until their closing brace. Anything the wire does not need is skipped
    rather than interpreted, so an unrecognised declaration is dropped quietly
    and shows up as a missing type at decode time, where it is loud.
    """
    src = _strip(text)
    model = Model()
    scope: list[str] = []
    i = 0
    n = len(src)
    pending_ext: str | None = None
    pending_key = False

    def qual(name: str) -> str:
        return ".".join(scope + [name]) if scope else name

    while i < n:
        m = re.compile(r"\s*").match(src, i)
        i = m.end()
        if i >= n:
            break

        # annotations
        a = re.compile(r"@(\w+)(?:\(([^)]*)\))?").match(src, i)
        if a:
            if a.group(1) == "extensibility":
                pending_ext = (a.group(2) or "").strip()
            elif a.group(1) == "key":
                pending_key = True
            i = a.end()
            continue

        mo = re.compile(r"module\s+([A-Za-z_]\w*)\s*\{").match(src, i)
        if mo:
            scope.append(mo.group(1))
            i = mo.end()
            continue

        if src[i] == "}":
            j = i + 1
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] == ";":
                j += 1
            if scope:
                scope.pop()
            i = j
            continue

        st = re.compile(r"(struct)\s+([A-Za-z_]\w*)\s*\{").match(src, i)
        if st:
            agg = Aggregate("struct", qual(st.group(2)),
                            pending_ext or "FINAL")
            pending_ext = None
            i, agg.members = _parse_members(src, st.end(), scope)
            model.aggregates[agg.name] = agg
            continue

        un = re.compile(
            r"union\s+([A-Za-z_]\w*)\s+switch\s*\(\s*([\w:]+)\s*\)\s*\{"
        ).match(src, i)
        if un:
            agg = Aggregate("union", qual(un.group(1)),
                            pending_ext or "FINAL")
            pending_ext = None
            agg.disc_type = _dot(un.group(2))
            i, agg.cases = _parse_cases(src, un.end(), scope)
            model.aggregates[agg.name] = agg
            continue

        en = re.compile(r"enum\s+([A-Za-z_]\w*)\s*\{(.*?)\}\s*;",
                        re.S).match(src, i)
        if en:
            agg = Aggregate("enum", qual(en.group(1)))
            nxt_val = 0
            for part in en.group(2).split(","):
                if not part.strip():
                    continue
                v = re.search(r"@value\s*\(\s*(-?\d+)\s*\)", part)
                nm = re.search(r"(?:@\w+\s*\([^)]*\)\s*)*([A-Za-z_]\w*)",
                               part.strip())
                if not nm:
                    continue
                val = int(v.group(1)) if v else nxt_val
                nxt_val = val + 1
                agg.enumerators.append(nm.group(1))
                agg.by_value[val] = nm.group(1)
            model.aggregates[agg.name] = agg
            pending_ext = None
            i = en.end()
            continue

        co = re.compile(r"const\s+[\w\s:]+?\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;"
                        ).match(src, i)
        if co:
            model.constants[qual(co.group(1))] = int(co.group(2))
            i = co.end()
            continue

        td = re.compile(r"typedef\s+(.+?)\s+([A-Za-z_]\w*)((?:\s*\[\s*\d+\s*\])*)\s*;",
                        re.S).match(src, i)
        if td:
            mem = _member_from(td.group(1).strip(), td.group(2),
                               td.group(3), scope)
            model.typedefs[qual(td.group(2))] = mem
            i = td.end()
            continue

        # Anything else: skip to the next statement boundary.
        nxt = src.find(";", i)
        brace = src.find("{", i)
        if nxt == -1:
            break
        if brace != -1 and brace < nxt:
            depth, j = 0, brace
            while j < n:
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        i = nxt + 1
        pending_key = False

    return model


def _dot(name: str) -> str:
    return name.replace("::", ".")


def _member_from(type_text: str, name: str, arr_text: str,
                 scope: list[str]) -> Member:
    """One declaration's wire shape."""
    bounds = [int(x) for x in re.findall(r"\[\s*(\d+)\s*\]", arr_text or "")]
    t = " ".join(type_text.split())
    seq = _SEQ.match(t)
    if seq:
        inner = seq.group(1).strip()
        raw_bound = seq.group(2)
        bound = int(raw_bound) if raw_bound and raw_bound.isdigit() else None
        mem = Member(name, _resolve_name(inner, scope), sequence=True,
                     seq_bound=bound)
        if raw_bound and not raw_bound.isdigit():
            mem.bound_name = raw_bound
        mem.array = bounds
        return mem
    if t.startswith("string") or t.startswith("wstring"):
        return Member(name, "string", array=bounds)
    return Member(name, _resolve_name(t, scope), array=bounds)


def _resolve_name(t: str, scope: list[str]) -> str:
    t = " ".join(t.split())
    if t in PRIMITIVES:
        return t
    if t.startswith("string") or t.startswith("wstring"):
        return "string"
    t = _dot(t)
    if "." in t:
        return t
    # Unqualified: nearest enclosing scope wins, so try longest prefix first.
    return t if not scope else ".".join(scope + [t])


def _parse_members(src: str, i: int,
                   scope: list[str]) -> tuple[int, list[Member]]:
    out: list[Member] = []
    n = len(src)
    pending_key = False
    while i < n:
        m = re.compile(r"\s*").match(src, i)
        i = m.end()
        if i < n and src[i] == "}":
            j = i + 1
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] == ";":
                j += 1
            return j, out
        a = re.compile(r"@(\w+)(?:\(([^)]*)\))?\s*").match(src, i)
        if a:
            if a.group(1) == "key":
                pending_key = True
            i = a.end()
            continue
        d = re.compile(
            r"([^;{}]+?)\s+([A-Za-z_]\w*)((?:\s*\[\s*\d+\s*\])*)\s*;"
        ).match(src, i)
        if not d:
            nxt = src.find(";", i)
            if nxt == -1:
                return n, out
            i = nxt + 1
            continue
        mem = _member_from(d.group(1).strip(), d.group(2), d.group(3), scope)
        mem.is_key = pending_key
        pending_key = False
        out.append(mem)
        i = d.end()
    return n, out


def _parse_cases(src: str, i: int,
                 scope: list[str]) -> tuple[int, list[tuple[list[str], Member]]]:
    out: list[tuple[list[str], Member]] = []
    n = len(src)
    while i < n:
        m = re.compile(r"\s*").match(src, i)
        i = m.end()
        if i < n and src[i] == "}":
            j = i + 1
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] == ";":
                j += 1
            return j, out
        labels: list[str] = []
        while True:
            c = re.compile(r"case\s+([\w:]+)\s*:\s*").match(src, i)
            if not c:
                break
            labels.append(c.group(1).split("::")[-1])
            i = c.end()
        dflt = re.compile(r"default\s*:\s*").match(src, i)
        if dflt:
            labels.append("default")
            i = dflt.end()
        if not labels:
            nxt = src.find(";", i)
            if nxt == -1:
                return n, out
            i = nxt + 1
            continue
        d = re.compile(
            r"([^;{}]+?)\s+([A-Za-z_]\w*)((?:\s*\[\s*\d+\s*\])*)\s*;"
        ).match(src, i)
        if not d:
            nxt = src.find(";", i)
            i = (nxt + 1) if nxt != -1 else n
            out.append((labels, Member("", "void")))
            continue
        out.append((labels, _member_from(d.group(1).strip(), d.group(2),
                                         d.group(3), scope)))
        i = d.end()
    return n, out
