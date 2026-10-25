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
from typing import Any, Dict, Optional, Type, TypeVar

from ..server import idlmodel, pin, webjson, xcdr2

__all__ = ["to_json", "from_json", "fqn_of", "key_member", "key_of",
           "validate", "SchemaInvalid", "model", "idl_order"]

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
    return m


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
        f"{name!r} is not a registered type name, a well-known message, or a "
        f"dotted type identity in the pinned IDL")


def to_json(sample: Any) -> Dict[str, Any]:
    """The N.1 document for a typed sample: the gateway's projection of its bytes.

    Raises `SchemaInvalid` for a sample N.1 cannot represent. A NaN or
    infinite double crosses the wire, and the projector passes it through,
    but N.1 is "finite only; NaN/Inf rejected" (MAPPING.md). The check is the
    gateway's own canonical form, the one its ETags are computed from, so
    the refusal and a served document's digest are the same rule.
    """
    dec, _w, proj, _l = _codec()
    fqn = fqn_of(sample)
    doc = proj.project(fqn, dec.decode(fqn, sample.serialize()))
    try:
        webjson.canon(doc)
    except ValueError as e:
        raise SchemaInvalid(f"{fqn} is not representable in N.1: {e}") from e
    return doc


def from_json(cls: Type[T], doc: Dict[str, Any]) -> T:
    """A typed sample from its N.1 document: the gateway's lift, encoded, decoded."""
    _d, writer, _p, lifter = _codec()
    fqn = fqn_of(cls)
    return cls.deserialize(writer.encode(fqn, lifter.lift(fqn, doc)))


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
    """
    try:
        import jsonschema
    except ImportError as e:
        from ._extra import EXTRA, MissingExtra
        raise MissingExtra(f"validation needs jsonschema: {EXTRA}") from e
    doc = to_json(sample)
    fqn = fqn_of(sample)
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
