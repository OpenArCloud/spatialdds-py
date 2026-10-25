"""One JSON: the gateway's own codec, run on native samples.

    from spatialdds.dds import to_json, from_json
    doc = to_json(sample)                    # N.1, exactly as the gateway serves it
    same = from_json(type(sample), doc)      # and back, to the same wire bytes

There is no JSON mapping in the native layer. `to_json` serializes the typed
sample to XCDR2 with cyclonedds and hands the bytes to the gateway's `xcdr2`
decoder and `webjson` projector, the same path a gateway serves a recording
through. `from_json` runs the gateway's lifter and XCDR2 writer and hands the
bytes to cyclonedds. So the canonical dialect exists in exactly one place, and a
divergence between the native and web layers cannot arise.

That divergence was measured: SpatialDDS-demo's own mapper, vendored by
openvps, writes unions as `{"discriminator": …}` and refused this package's
`GeoAnchor` builder output (docs/NATIVE-SDK-CENSUS.md, subject 8).

The model is every type in the vendored IDL, parsed by the gateway's parser,
so a type the bindings carry is a type this can map. `gates/native_types.py`
holds the two sets equal.
"""
from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type, TypeVar

from ..server import idlmodel, pin, webjson, xcdr2

__all__ = ["to_json", "from_json", "fqn_of", "key_member", "key_of",
           "validate", "SchemaInvalid", "model", "idl_order", "class_for",
           "extend", "Extension", "extensions", "extension_of"]

T = TypeVar("T")


class SchemaInvalid(ValueError):
    """A sample whose N.1 form its published JSON Schema rejects."""


def idl_order() -> list:
    """Every vendored IDL file, each after the files it includes."""
    files = sorted(p.relative_to(pin.IDL_DIR).as_posix()
                   for p in pin.IDL_DIR.rglob("*.idl"))
    out: list = []

    def visit(f: str) -> None:
        if f in out:
            return
        for inc in re.findall(r'^#include\s+"([^"]+)"',
                              (pin.IDL_DIR / f).read_text(), re.M):
            if inc in files:
                visit(inc)
        out.append(f)
    for f in files:
        visit(f)
    return out


@lru_cache(maxsize=1)
def model() -> idlmodel.Model:
    """The whole vendored IDL, through the gateway's parser."""
    m = idlmodel.parse(pin.flatten(pin.IDL_DIR, idl_order()))
    m._leaves = None
    #: The pin's own types, before any `extend()` adds to this model. One
    #: extension's IDL can define several types, so "in the model" is not
    #: "pinned" once the first is registered.
    m.pinned = frozenset(m.aggregates)
    return m


@dataclass(frozen=True)
class Extension:
    """A deployment's own type, registered with `extend()`."""
    type_name: str      # its §3.3.2-style name, namespaced: "oarc.fusion_coverage"
    cls: type           # the generated class
    fqn: str            # its dotted identity, "oarc_demo.FusionCoverage"
    record: bytes       # the flattened IDL a recording embeds for it


_EXT: Dict[str, Extension] = {}
_EXT_FQN: Dict[str, Extension] = {}
_EXT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def extensions() -> Dict[str, Extension]:
    """Every type registered with `extend()`, by name."""
    return dict(_EXT)


def extension_of(fqn: str) -> Optional[Extension]:
    """The registered extension with this dotted identity, if there is one."""
    return _EXT_FQN.get(fqn)


def extend(type_name: str, cls: type, idl: Any) -> Extension:
    """Teach this layer a deployment's own type, beside the pinned IDL.

    A deployment's extension (§3.3.2 allows one, documented) is a type the
    pinned IDL does not define. Until it is registered here, the native
    layer can write it but cannot convert it to N.1 or back, find its key,
    name its class, or record its schema. Registering it gives it all four:

    * `to_json` / `from_json`, through the same gateway codec as every
      pinned type;
    * `key_of` from its own `@key`, so instances are tracked and `on_exit`
      applies;
    * `class_for(type_name)`, for a bridge that learns the type from an
      announce;
    * the recording tap embeds its IDL.

    `idl` is the IDL text, or a path to it. It may `#include` only pinned
    files, and its schema record is their closure, then its own body. It must
    define the class's own type.

    `validate()` cannot check an extension against a published schema,
    because none exists. It checks that the sample is representable in N.1,
    and says so.

    Refused:

    * a name without a namespace, which could collide with a future
      registration;
    * a registered name, or a well-known message;
    * a pinned type;
    * IDL that includes an unpinned file, or does not define the class;
    * the same name again with different IDL or class.

    Registering again with identical arguments is a no-op.
    """
    from pathlib import Path
    from . import registry
    problems = []
    if type_name in registry.registrations() \
            or type_name in registry.well_known():
        problems.append(f"{type_name!r} is the spec's (§3.3.2 or a well-known "
                        f"message): it needs no extension")
    elif not isinstance(type_name, str) or not _EXT_NAME.match(type_name):
        problems.append(f"extension name {type_name!r} must be namespaced, "
                        f"lower-case segments joined by '.' (e.g. "
                        f"'oarc.fusion_coverage'), so it cannot collide with a "
                        f"name §3.3.2 registers later")
    try:
        fqn = fqn_of(cls)
    except TypeError as e:
        problems.append(str(e))
        fqn = ""
    if fqn and fqn in model().pinned:
        problems.append(f"{fqn} is in the pinned IDL: use its registered name")
    text = idl
    if isinstance(idl, Path) or (isinstance(idl, str) and "\n" not in idl
                                 and Path(idl).is_file()):
        text = Path(idl).read_text()
    if not isinstance(text, str) or not text.strip():
        problems.append("idl= must be IDL text or a path to it")
        text = ""
    files: list = []
    for inc in re.findall(r'^\s*#include\s+"([^"]+)"', text, re.M):
        if not (pin.IDL_DIR / inc).is_file():
            problems.append(f"the extension's IDL includes {inc!r}, which the "
                            f"pinned IDL does not have")
            continue
        for f in pin.file_closure(inc):
            if f not in files:
                files.append(f)
    if problems:
        raise registry.RegistryError("; ".join(problems))
    body = "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))
    record = (pin.flatten(pin.IDL_DIR, files)
              + f"\n// ---- extension {type_name} ----\n{body}").encode()
    parsed = idlmodel.parse(record.decode())
    if fqn not in parsed.aggregates:
        raise registry.RegistryError(
            f"the extension's IDL does not define {fqn}, the type "
            f"{cls.__name__} says it is")
    prior = _EXT.get(type_name) or _EXT_FQN.get(fqn)
    if prior is not None:
        if (prior.type_name, prior.cls, prior.record) == (type_name, cls, record):
            return prior
        raise registry.RegistryError(
            f"{prior.type_name} ({prior.fqn}) is already registered, "
            f"differently: one name, one type, one IDL")
    m = model()
    for k, v in parsed.aggregates.items():
        m.aggregates.setdefault(k, v)
    for k, v in parsed.typedefs.items():
        m.typedefs.setdefault(k, v)
    m._leaves = None
    _codec.cache_clear()
    key_member.cache_clear()
    ext = Extension(type_name, cls, fqn, record)
    _EXT[type_name] = _EXT_FQN[fqn] = ext
    return ext


def _known(fqn: str) -> None:
    if fqn not in model().aggregates:
        from . import registry
        raise registry.RegistryError(
            f"{fqn} is not in the pinned IDL: register a deployment's own type "
            f"with sd.dds.extend(name, cls, idl) first")


@lru_cache(maxsize=1)
def _codec():
    m, tables = model(), webjson.tables()
    return (xcdr2.Decoder(m), xcdr2.Writer(m), webjson.Projector(m, tables),
            webjson.Lifter(m, tables))


def fqn_of(obj_or_cls: Any) -> str:
    """The dotted type identity, `spatial.core.GeoAnchor`, of a generated type."""
    cls = obj_or_cls if isinstance(obj_or_cls, type) else type(obj_or_cls)
    name = getattr(cls, "__idl_typename__", None)
    if not name:
        raise TypeError(f"{cls.__name__} is not a generated SpatialDDS type")
    return name.replace("::", ".")


def class_for(name: str) -> type:
    """The generated class for a type, by any name this layer knows it by.

    * a registered §3.3.2 type name (`"model_entity"`);
    * a well-known message (`"Announce"`);
    * a dotted identity (`"spatial.core.GeoPose"`).

    For a program that learns a type at run time, a bridge reading an
    announce's `TopicMeta.type` say, rather than importing a class it knew
    about beforehand. An unknown name raises `RegistryError` saying which
    forms were tried.
    """
    import importlib
    from . import registry
    ext = _EXT.get(name) or _EXT_FQN.get(name)
    if ext is not None:
        return ext.cls
    reg = registry.registrations().get(name)
    wk = registry.well_known() if name in registry.well_known() else None
    fqn = reg.fqn if reg else wk[name].fqn if wk else name
    mod, _, cls = fqn.rpartition(".")
    if mod.startswith("spatial") or mod == "builtin":
        try:
            return getattr(importlib.import_module(
                "spatialdds.dds.types." + mod), cls)
        except (ImportError, AttributeError):
            pass
    raise registry.RegistryError(
        f"{name!r} is not a registered type name, a well-known message, a "
        f"dotted type identity in the pinned IDL, or an extension registered "
        f"with extend()")


def to_json(sample: Any) -> Dict[str, Any]:
    """The N.1 document for a typed sample: the gateway's projection of its bytes.

    Raises `SchemaInvalid` for a sample N.1 cannot represent. A NaN or
    infinite double crosses the wire, and the projector passes it through,
    but N.1 is "finite only; NaN/Inf rejected" (MAPPING.md). The check is the
    gateway's own canonical form, the one its ETags are computed from, so
    the refusal and a served document's digest are the same rule.
    """
    fqn = fqn_of(sample)
    _known(fqn)
    dec, _w, proj, _l = _codec()
    doc = proj.project(fqn, dec.decode(fqn, sample.serialize()))
    try:
        webjson.canon(doc)
    except ValueError as e:
        raise SchemaInvalid(f"{fqn} is not representable in N.1: {e}") from e
    return doc


#: The wire layer's refusal: a document or sample that cannot be its type's
#: wire form (a member missing, say). A `ValueError` (0.7.3, S13).
WireError = xcdr2.WireError


class _StrictLifter(webjson.Lifter):
    """The gateway's lift, noting every member it was given and did not read."""

    def __init__(self, m, tables) -> None:
        super().__init__(m, tables)
        self.unread: list = []

    def lift(self, fqn: str, value: Any) -> Any:
        agg = self.m.get(fqn)
        if agg is not None and agg.kind == "struct" and isinstance(value, dict):
            names = {mem.name for mem in agg.members}
            self.unread += [f"{fqn}.{k}" for k in value if k not in names]
        return super().lift(fqn, value)

    def _union(self, agg, value: dict) -> dict:
        if isinstance(value, dict):
            label = value.get("type")
            case = next((mem.name for labels, mem in agg.cases
                         if label in labels and mem.name), None)
            self.unread += [f"{agg.name}.{k}" for k in value
                            if k not in ("type", case)]
        return super()._union(agg, value)


def from_json(cls: Type[T], doc: Dict[str, Any], *, strict: bool = False) -> T:
    """A typed sample from its N.1 document: the gateway's lift, encoded, decoded.

    A member the type does not have is dropped, as the gateway drops it: the
    published schemas allow additional properties, so a newer writer's
    document still reads. `strict=True` refuses one instead, with
    `SchemaInvalid` naming each, for a program that authors documents rather
    than relaying them, where an unknown member is a misspelling and dropping
    it loses what was meant (0.7.3, S12). A document that is missing a
    member, or has one of the wrong form, is a `ValueError` either way.
    """
    fqn = fqn_of(cls)
    _known(fqn)
    _d, writer, _p, lifter = _codec()
    if strict:
        lifter = _StrictLifter(lifter.m, lifter.tables)
    lifted = lifter.lift(fqn, doc)
    if strict and lifter.unread:
        raise SchemaInvalid(f"{fqn}: no such member: "
                            f"{', '.join(lifter.unread)} (strict=True)")
    return cls.deserialize(writer.encode(fqn, lifted))


@lru_cache(maxsize=None)
def key_member(fqn: str) -> Optional[str]:
    """The member the IDL marks `@key`: the gateway's instance name.

    The first one, which is the gateway's rule (`gateway.build_state`), so a
    native key and a gateway path name the same instance. None for an unkeyed
    type.
    """
    agg = model().get(fqn)
    keys = [m.name for m in (agg.members if agg else []) if m.is_key]
    return keys[0] if keys else None


def key_of(sample: Any) -> str:
    """The instance name of a sample, as the gateway names it; "" if unkeyed."""
    k = key_member(fqn_of(sample))
    return "" if k is None else str(getattr(sample, k))


def validate(sample: Any) -> Dict[str, Any]:
    """Check a sample against everything a reader of it will: return its N.1 form.

    Three refusals, in the order they can happen. A sample that cannot be its
    type's wire form (a 3-element quaternion, an over-bound sequence) fails
    to serialize. One whose wire form N.1 cannot carry (a non-finite double)
    is `SchemaInvalid` from `to_json`. One whose N.1 form breaks its published
    schema is `SchemaInvalid` here.

    An extension registered with `extend()` has no published schema, so its
    check stops at the second: representable in N.1.
    """
    try:
        import jsonschema
    except ImportError as e:
        from ._extra import EXTRA, MissingExtra
        raise MissingExtra(f"validation needs jsonschema: {EXTRA}") from e
    doc = to_json(sample)
    fqn = fqn_of(sample)
    if fqn in _EXT_FQN:
        return doc
    schema = webjson.schemas().get(fqn)
    if schema is None:
        raise SchemaInvalid(f"no published schema for {fqn}, so a sample "
                            f"cannot be validated; pass validate=False to "
                            f"write it unchecked")
    try:
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise SchemaInvalid(f"{fqn} at {where}: {e.message}") from e
    return doc
