"""Record builders: natural arguments in, canonical records out.

The gap this closes. Appendix N's schemas make every member of a record
required, so publishing a service announcement by hand means writing out
eighteen members -- `coverage_eval_time` zeroed, `has_coverage_eval_time`
false, `transforms` empty -- before the interesting three. The authored
inputs in this repository's `samples/` are that work done once, by hand, and
`samples/duck.input.json` is 170 lines to place one duck.

So: `service_announce`, `content_manifest`, `content_announce`, `geo_anchor`,
and the geometry helpers `circle` and `bbox`. Each returns a plain dict in
Appendix N.1 canonical form -- publishable as it stands, dumpable as JSON for
the authoring tools, and nothing clever: no classes to learn, no builder
objects, no validation framework.

**Completeness is by construction.** `_blank` reads the generated serializer
tables and produces every member of a type at its zero value; a builder then
overlays what it means. A member added by a future revision therefore shows up
zeroed rather than missing, which is the difference between a record that
validates and a 400.

**Conventions fill plumbing. They never fill claims.** A stamp of now, an
earth-fixed frame, a checksum computed from the record itself: those are
plumbing, and filling them saves a caller from writing out what has only one
sensible value. A profile list, a rate limit, a request topic, an auth scheme,
a title: those are claims about what a service can do or what content is, and
no default is honest for any of them. A caller who states nothing publishes a
minimal record, and that record is *correct*; a caller who states a contract
publishes it in one call. Nothing in between is invented.

**What the builders never fill: identity.** `provider_id` and `org` come from
the caller or stay empty. Who published a record, and whether a registry may
say so on their behalf, is the identity design that is deferred -- see the 1.9
candidates list. A builder that invented a provider id would be inventing
provenance.

**Authority is stated, never guessed.** Every `spatialdds://` URI these
builders assemble needs an `authority=`, because an authority is a DNS name
somebody has to own. The endpoint verbs (`ep.publish_service` and friends) fill
it from the endpoint's own resolver metadata -- the endpoint saying who it is,
which is not a guess -- and nothing else does.

**Validation before the wire.** `_check` walks the same tables and raises
`ValueError` naming the field. It is a courtesy, not the authority: the
endpoint validates every body against the generated JSON Schema on arrival and
refuses with a problem document. Two independent checks on the path, and the
local one needs no dependency -- the client half has two and this is not one
of them.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from pathlib import Path
import time as _time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import canonical, canonjson

__all__ = [
    "circle", "bbox", "earth_fixed", "frame", "stamp", "cov_pos3", "cov_none",
    "geopose", "topic", "asset", "asset_ref", "profile", "capabilities",
    "entity", "model_pose",
    "service_announce", "service_manifest", "content_manifest",
    "content_announce", "geo_anchor", "cells", "service_uri", "content_uri",
    "MANIFEST_PROFILE",
]

#: The profile string N.7 manifests in this binding carry.
MANIFEST_PROFILE = "spatial.manifest/1.8"

ANNOUNCE = "spatial.disco.Announce"
CONTENT_ANNOUNCE = "spatial.disco.ContentAnnounce"
GEO_ANCHOR = "spatial.core.GeoAnchor"
COVERAGE = "spatial.disco.CoverageElement"
FRAME_REF = "spatial.common.FrameRef"
TOPIC_META = "spatial.disco.TopicMeta"
GEOPOSE = "spatial.core.GeoPose"
ENTITY = "spatial.owm.Entity"
MODEL_POSE = "spatial.owm.ModelPose"
TIME = "builtin.Time"

_GEOHASH32 = "0123456789bcdefghjkmnpqrstuvwxyz"


# ---- the table-driven floor ------------------------------------------------

def _tables() -> Dict[str, Any]:
    return canonical.tables()


def _zero(desc: Dict[str, Any], depth: int = 0) -> Any:
    t = desc.get("t")
    if t in ("int64", "uint64"):
        return "0"                       # N.1: unconditional decimal string
    if t == "int":
        return 0
    if t == "double":
        return 0.0
    if t == "bool":
        return False
    if t in ("str", "base64"):
        return ""
    if t == "enum":
        # The first enumerator, and only ever where a `has_` flag says the
        # value is absent. Canonical JSON has no null, so an absent enum still
        # needs a member; the flag beside it is what carries the absence.
        members = _tables()[desc["fqn"]]["members"]
        return members[0]
    if t == "ref":
        return _blank(desc["fqn"], depth + 1)
    if t == "union":
        return _blank_union(desc["fqn"])
    if t == "array":
        n = desc.get("length")
        if not n:
            return []
        return [_zero(desc["item"], depth + 1) for _ in range(n)]
    return ""


def _blank_union(fqn: str) -> Dict[str, Any]:
    """A union at its first case, which for every covariance is the empty one."""
    e = _tables()[fqn]
    label, name, desc = e["cases"][0]
    out = {"type": label}
    # A union case may carry no member at all -- `COV_NONE` is exactly that,
    # and it is the case every blank covariance wants. The tables spell it as
    # a null descriptor, so the guard is on the descriptor and not the name.
    if name and desc:
        out[name] = _zero(desc)
    return out


def _blank(fqn: str, depth: int = 0) -> Dict[str, Any]:
    """Every member of a type, at its zero. The floor a builder overlays.

    Read from the generated tables rather than written out here, so that a
    member the specification adds appears zeroed in the next re-vendor instead
    of being silently absent from every record this library builds.
    """
    if depth > 8:
        return {}
    e = _tables().get(fqn)
    if e is None:
        raise ValueError(f"no serializer table for {fqn}")
    if e.get("kind") == "union":
        return _blank_union(fqn)
    return {n: _zero(d, depth) for n, d in e["fields"]}


def _check(fqn: str, doc: Dict[str, Any], where: str = "") -> None:
    """Every member present and of the right JSON shape, or a named field.

    Deliberately not jsonschema: the client half declares two dependencies and
    this is not one of them. The tables carry the same per-field facts, and the
    endpoint validates against the generated schemas on arrival anyway, so the
    purpose here is a useful message before anything is sent.
    """
    e = _tables().get(fqn)
    if e is None:
        raise ValueError(f"no serializer table for {fqn}")
    if e.get("kind") == "union":
        if "type" not in doc:
            raise ValueError(f"{where or fqn}: a union needs a `type` tag")
        return
    for n, d in e["fields"]:
        at = f"{where}.{n}" if where else f"{fqn}.{n}"
        if n not in doc:
            raise ValueError(
                f"{at} is missing. Appendix N makes every member of a record "
                f"required, so a body is a whole record and never a patch.")
        v, t = doc[n], d.get("t")
        if t in ("int64", "uint64"):
            if not isinstance(v, str) or not v.lstrip("-").isdigit():
                raise ValueError(
                    f"{at} must be a decimal string, not {v!r}: N.1 carries "
                    f"int64 and uint64 as strings so that no JSON reader "
                    f"rounds them.")
        elif t == "int":
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(f"{at} must be an integer, not {v!r}")
        elif t == "double":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"{at} must be a number, not {v!r}")
        elif t == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{at} must be true or false, not {v!r}")
        elif t in ("str", "base64"):
            if not isinstance(v, str):
                raise ValueError(f"{at} must be a string, not {v!r}")
        elif t == "enum":
            members = _tables()[d["fqn"]]["members"]
            if v not in members:
                raise ValueError(
                    f"{at} is {v!r}, which is not one of "
                    f"{', '.join(members)}")
        elif t == "ref":
            _check(d["fqn"], v, at)
        elif t == "union":
            _check(d["fqn"], v, at)
        elif t == "array":
            if not isinstance(v, list):
                raise ValueError(f"{at} must be a list, not {v!r}")
            n_fixed = d.get("length")
            if n_fixed and len(v) != n_fixed:
                raise ValueError(
                    f"{at} must hold exactly {n_fixed} values, not {len(v)}")
            item = d["item"]
            if item.get("t") in ("ref", "union"):
                for i, x in enumerate(v):
                    _check(item["fqn"], x, f"{at}[{i}]")


# ---- time, frames, geometry ------------------------------------------------

def stamp(when: Any = None) -> Dict[str, Any]:
    """A `Time`, now unless given.

    Accepts a datetime, an epoch number, a decimal string of epoch seconds
    (the form N.1 serves), or an ISO 8601 string.

    The convention the brief asked for, in one place: a record with no stamp
    is stamped at the moment it was built, because an unstamped record is
    indistinguishable from one stamped at the epoch.
    """
    if when is None:
        t = _time.time()
    elif isinstance(when, _dt.datetime):
        w = when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)
        t = w.timestamp()
    elif isinstance(when, str) and when.lstrip("-").isdigit():
        # A decimal string is epoch seconds, because that is the form N.1
        # puts `sec` in: a stamp read off a served record goes straight back
        # in here without the caller converting anything.
        t = float(when)
    elif isinstance(when, str):
        w = _dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        t = (w if w.tzinfo else w.replace(tzinfo=_dt.timezone.utc)).timestamp()
    else:
        t = float(when)
    return {"sec": str(int(t)), "nanosec": int(round((t % 1) * 1e9))}


def earth_fixed() -> Dict[str, Any]:
    """The default frame: WGS84, no local convention, no scale claim.

    Chosen rather than left empty because a `FrameRef` with an empty `fqn`
    says nothing at all, and every builder here takes positions in degrees.
    """
    fr = _blank(FRAME_REF)
    fr["fqn"] = "earth-fixed"
    return fr


def frame(fqn: str, *, uuid: str = "", convention: Optional[str] = None,
          meters_per_unit: Optional[float] = None,
          scale_status: str = "SCALE_DECLARED",
          display_unit: str = "m") -> Dict[str, Any]:
    """A named frame, with the `has_` flags set by what you actually passed."""
    fr = _blank(FRAME_REF)
    fr["fqn"], fr["uuid"] = fqn, uuid
    if convention is not None:
        fr["has_coord_convention"] = True
        fr["coord_convention"] = convention
    if meters_per_unit is not None:
        fr["has_scale"] = True
        fr["scale_status"] = scale_status
        fr["meters_per_unit"] = float(meters_per_unit)
        fr["display_unit"] = display_unit
    return fr


def circle(lat: float, lon: float, radius_m: float, *, alt_m: float = 0.0,
           crs: str = "EPSG:4326",
           frame_ref: Optional[Dict[str, Any]] = None,
           frame_declared: bool = True) -> Dict[str, Any]:
    """A coverage element: this much ground around a point.

    `circle_center` is `[lon, lat, alt]` -- x, y, z, the order the IDL's
    comment gives and the order every committed record uses. The argument
    order here is `lat, lon` because that is the order people say, and
    getting the two confused is the kind of mistake that puts a service in
    the ocean.

    `frame_declared=False` carries a `frame_ref` body while leaving
    `has_frame_ref` false: the element names the frame it was expressed in
    without asserting it as the coverage's own, which is what every committed
    record in this repository does. Absence of the flag is the claim; the
    body beside it is information.
    """
    c = _blank(COVERAGE)
    c["has_crs"], c["crs"] = True, crs
    c["has_circle"] = True
    c["circle_center"] = [float(lon), float(lat), float(alt_m)]
    c["circle_radius_m"] = float(radius_m)
    if frame_ref is not None:
        c["has_frame_ref"], c["frame_ref"] = bool(frame_declared), frame_ref
    return c


def bbox(west: float, south: float, east: float, north: float, *,
         crs: str = "EPSG:4326",
         frame_ref: Optional[Dict[str, Any]] = None,
         frame_declared: bool = True) -> Dict[str, Any]:
    """A coverage element: this box of ground. §3.3.0's own bbox order.

    `frame_declared` as in `circle`: false carries the frame as information
    rather than as the coverage's own assertion.
    """
    c = _blank(COVERAGE)
    c["has_crs"], c["crs"] = True, crs
    c["has_bbox"] = True
    c["bbox"] = [float(west), float(south), float(east), float(north)]
    if frame_ref is not None:
        c["has_frame_ref"], c["frame_ref"] = bool(frame_declared), frame_ref
    return c


def _coverage(coverage: Any) -> List[Dict[str, Any]]:
    if coverage is None:
        return []
    if isinstance(coverage, dict):
        return [coverage]
    return list(coverage)


def cells(coverage: Any, precision: int = 6) -> List[str]:
    """The geohash cells a coverage declaration will be found under.

    Advisory, and said plainly: **no 1.8 record carries geohash cells.** A
    §3.3.0 search expands the client's cell to a bounding box and the endpoint
    intersects it with the coverage a record declares, so the cells are a
    consequence of the geometry rather than a field. This exists so a
    publisher can check which query will find what they just published --
    `examples/publish_service.py` prints it -- and so the convention the brief
    asked for is computed from the declared coverage rather than typed by
    hand.
    """
    out: List[str] = []
    for c in _coverage(coverage):
        if c.get("has_circle"):
            lon, lat = c["circle_center"][0], c["circle_center"][1]
        elif c.get("has_bbox"):
            w, s, e, n = c["bbox"]
            lon, lat = (w + e) / 2, (s + n) / 2
        else:
            continue
        gh = _geohash(lat, lon, precision)
        if gh not in out:
            out.append(gh)
    return out


def _geohash(lat: float, lon: float, precision: int) -> str:
    lo, hi, lo2, hi2 = -90.0, 90.0, -180.0, 180.0
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lo2 + hi2) / 2
            ch, lo2, hi2 = ((ch * 2 + 1, mid, hi2) if lon > mid
                            else (ch * 2, lo2, mid))
        else:
            mid = (lo + hi) / 2
            ch, lo, hi = ((ch * 2 + 1, mid, hi) if lat > mid
                          else (ch * 2, lo, mid))
        even = not even
        bit += 1
        if bit == 5:
            out.append(_GEOHASH32[ch])
            bit, ch = 0, 0
    return "".join(out)


def cov_none() -> Dict[str, Any]:
    """No covariance stated. Absence as data, not as a missing member."""
    return {"type": "COV_NONE"}


def cov_pos3(sigma_e: float, sigma_n: float, sigma_u: float, *,
             rho_en: float = 0.0) -> Dict[str, Any]:
    """A positional covariance from standard deviations in metres, ENU.

    Row-major 3x3, which is what `COV_POS3` means. Correlation between east
    and north is the one cross-term worth offering; the rest stay zero rather
    than being invented.
    """
    se, sn, su = float(sigma_e), float(sigma_n), float(sigma_u)
    en = float(rho_en) * se * sn
    return {"type": "COV_POS3",
            "pos": [se * se, en, 0.0, en, sn * sn, 0.0, 0.0, 0.0, su * su]}


def geopose(lat: float, lon: float, alt_m: float = 0.0, *,
            q: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
            when: Any = None,
            cov: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A GeoPose: where, which way round, when, and how sure."""
    gp = _blank(GEOPOSE)
    gp["lat_deg"], gp["lon_deg"], gp["alt_m"] = float(lat), float(lon), \
        float(alt_m)
    gp["q"] = [float(x) for x in q]
    if len(gp["q"]) != 4:
        raise ValueError(f"geopose q must hold 4 values, not {len(gp['q'])}")
    gp["stamp"] = stamp(when)
    gp["cov"] = cov if cov is not None else cov_none()
    return gp


def profile(name: str, *, major: int = 1, minor: Optional[int] = None,
            min_minor: Optional[int] = None,
            max_minor: Optional[int] = None) -> Dict[str, Any]:
    """One entry in `Capabilities.supported_profiles`.

    A version range is a claim about what this service implements, so there is
    no default for it: pass `minor=8` for a single version, or `min_minor` and
    `max_minor` for a range. `major` defaults to 1 because SpatialDDS is 1.x
    and that is arithmetic rather than a claim.
    """
    if minor is None and (min_minor is None or max_minor is None):
        raise ValueError(
            f"profile({name!r}) needs a version: minor=8, or min_minor= and "
            f"max_minor= for a range. A version range is a claim about what "
            f"this service implements, so this library will not guess one.")
    lo = min_minor if min_minor is not None else minor
    hi = max_minor if max_minor is not None else minor
    return {"name": name, "major": int(major), "min_minor": int(lo),
            "max_minor": int(hi)}


def capabilities(*, supported: Iterable[Dict[str, Any]] = (),
                 preferred: Iterable[str] = (),
                 features: Iterable[str] = ()) -> Dict[str, Any]:
    """A `Capabilities`: what this service supports, prefers and offers.

    Every member is what the caller said. An empty `Capabilities` is a service
    that claims nothing, which is a true statement about most services and the
    right default for all of them.
    """
    return {"supported_profiles": [dict(p) for p in supported],
            "preferred_profiles": list(preferred),
            "features": list(features)}


def topic(type_name: str, *, scene: str, stream: Optional[str] = None,
          version: str = "v1", durability: Optional[str] = None,
          qos_profile: str = "", rate_hz: float = 0.0,
          max_chunk_bytes: int = 0) -> Dict[str, Any]:
    """One `TopicMeta`, with the §3.3.1 topic name built from its segments.

    The name comes from `spatialdds.dds.registry.topic_name`, the one function
    the native layer names its topics with. So a registered type whose §3.3.2
    row gives a template (`model_entity` -> `spatialdds/<scene>/world/entity/v1`)
    gets that name, and `stream` may then be omitted.

    `durability` is derived, never guessed:

    * a registered §3.3.3 `qos_profile` decides it, and an explicit
      `durability` that contradicts the profile is refused;
    * with no profile, TRANSIENT_LOCAL, because the things a registry holds
      are definitions a late joiner has to receive;
    * an extension profile this package cannot read leaves it empty, which the
      spec says means absent.

    It is a member rather than an assumption as of the 1.8 revision (spec
    9c3e1f2). Until 0.7.0 the default was TRANSIENT_LOCAL whatever the profile
    said, so a `POSE_RT` row claimed a durability its profile contradicts
    (docs/NATIVE-SDK-CENSUS.md, filed against this repository).
    """
    from .dds import registry
    try:
        name = registry.topic_name(scene, stream, type_name, version)
    except registry.RegistryError as e:
        raise ValueError(str(e)) from None
    derived = None
    if qos_profile:
        known = registry.profiles().get(qos_profile)
        derived = known.durability if known is not None else ""
    if durability is None:
        durability = "TRANSIENT_LOCAL" if derived is None else derived
    elif derived and durability != derived:
        raise ValueError(
            f"durability {durability!r} contradicts {qos_profile}, which is "
            f"{derived} (§3.3.3)")
    tm = _blank(TOPIC_META)
    tm["name"] = name
    tm["type"], tm["version"] = type_name, version
    tm["durability"], tm["qos_profile"] = durability, qos_profile
    tm["target_rate_hz"] = float(rate_hz)
    tm["max_chunk_bytes"] = int(max_chunk_bytes)
    return tm


# ---- URIs, assembled under a stated authority ------------------------------

def _uri(authority: str, scene: str, kind: str, ident: str) -> str:
    """`spatialdds://<authority>/<scene>/<kind>/<name>`.

    `ident` may be a record's own id, and a record's id often carries the kind
    as a prefix: the committed duck's `content_id` is `content/duck` while its
    manifest answers to `.../content/duck`. So the URI takes the id's last
    segment, because the kind is already a segment of the path and repeating
    it would address a manifest nobody holds.
    """
    ident = ident.strip("/").rsplit("/", 1)[-1]
    if not authority:
        raise ValueError(
            "a spatialdds:// URI needs an authority, and this library will "
            "not invent one: it is a DNS name somebody owns. Pass "
            "authority=, or let an Endpoint fill it from the resolver "
            "metadata the endpoint publishes about itself.")
    return f"spatialdds://{authority}/{scene}/{kind}/{ident}"


def service_uri(authority: str, scene: str, service_id: str) -> str:
    """The URI a service manifest answers to."""
    return _uri(authority, scene, "service", service_id)


def content_uri(authority: str, scene: str, content_id: str) -> str:
    """The URI a content manifest answers to."""
    return _uri(authority, scene, "content", content_id)


# ---- the records -----------------------------------------------------------

def service_announce(service_id: str, kind: str, *, coverage: Any,
                     authority: str = "", scene: str = "",
                     name: str = "", org: str = "", version: str = "",
                     topics: Iterable[Dict[str, Any]] = (),
                     manifest_uri: Optional[str] = None,
                     auth_hint: str = "", ttl_sec: int = 86400,
                     hints: Optional[Dict[str, str]] = None,
                     caps: Optional[Dict[str, Any]] = None,
                     coverage_frame_ref: Optional[Dict[str, Any]] = None,
                     when: Any = None) -> Dict[str, Any]:
    """A `spatial.disco.Announce`: a service, where it works, what it serves.

    `manifest_uri` defaults to the URI a service manifest for this id would
    answer to, when an authority and a scene are known, and to "" otherwise --
    the empty string being how 1.8 says "no reference", with no `has_` guard.
    """
    rec = _blank(ANNOUNCE)
    rec["service_id"], rec["kind"] = service_id, kind
    rec["name"], rec["org"], rec["version"] = name or service_id, org, version
    rec["topics"] = list(topics)
    rec["coverage"] = _coverage(coverage)
    rec["coverage_frame_ref"] = coverage_frame_ref or earth_fixed()
    rec["auth_hint"] = auth_hint
    rec["ttl_sec"] = int(ttl_sec)
    rec["stamp"] = stamp(when)
    rec["hints"] = [{"key": k, "value": v}
                    for k, v in sorted((hints or {}).items())]
    if caps is not None:
        rec["caps"] = caps
    if manifest_uri is not None:
        rec["manifest_uri"] = manifest_uri
    elif authority and scene:
        rec["manifest_uri"] = service_uri(authority, scene, service_id)
    _check(ANNOUNCE, rec)
    return rec


def content_announce(content_id: str, *, coverage: Any,
                     authority: str = "", scene: str = "",
                     title: str = "", summary: str = "",
                     tags: Iterable[str] = (), class_id: str = "",
                     provider_id: str = "",
                     manifest_uri: Optional[str] = None,
                     ttl_sec: int = 3600,
                     coverage_frame_ref: Optional[Dict[str, Any]] = None,
                     available_from: Any = None,
                     available_until: Any = None,
                     when: Any = None) -> Dict[str, Any]:
    """A `spatial.disco.ContentAnnounce`: content, where it is, what it is.

    `provider_id` stays empty unless you pass one. Who published a thing is
    identity, and identity is deferred.
    """
    rec = _blank(CONTENT_ANNOUNCE)
    rec["content_id"], rec["provider_id"] = content_id, provider_id
    rec["title"], rec["summary"] = title or content_id, summary
    rec["tags"], rec["class_id"] = list(tags), class_id
    rec["coverage"] = _coverage(coverage)
    rec["coverage_frame_ref"] = coverage_frame_ref or earth_fixed()
    rec["ttl_sec"] = int(ttl_sec)
    rec["stamp"] = stamp(when)
    if available_from is not None:
        rec["available_from"] = stamp(available_from)
    if available_until is not None:
        rec["available_until"] = stamp(available_until)
    if manifest_uri is not None:
        rec["manifest_uri"] = manifest_uri
    elif authority and scene:
        rec["manifest_uri"] = content_uri(authority, scene, content_id)
    _check(CONTENT_ANNOUNCE, rec)
    return rec


def _pose(position: Sequence[float], q: Sequence[float]) -> Dict[str, Any]:
    t, r = [float(x) for x in position], [float(x) for x in q]
    if len(t) != 3 or len(r) != 4:
        raise ValueError(f"a pose is 3 coordinates and a 4-element quaternion "
                         f"(x, y, z, w); got {len(t)} and {len(r)}")
    return {"t": t, "q": r}


def entity(entity_id: str, *, frame_ref: Dict[str, Any], source_id: str,
           position: Optional[Sequence[float]] = None,
           q: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
           extent: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
           basis: str = "OBSERVED", layer: str = "FAST",
           type_uris: Iterable[str] = (),
           properties: Optional[Dict[str, str]] = None,
           state: str = "ACTIVE", state_reason: str = "",
           when: Any = None) -> Dict[str, Any]:
    """A `spatial.owm.Entity` (provisional `spatial.owm/0.1`): a thing in a frame.

    `frame_ref` and `source_id` are required: a pose with no frame says
    nowhere, and the world model is built from who claims what. `position`
    is optional, and `has_pose` says whether it was given, which is the
    literal guard N.1 asks for rather than a zero pose that claims the origin.
    `extent` is `(min_xyz, max_xyz)`, the on-bus `Aabb3`, with `has_extent`
    set the same way.
    `basis` defaults to OBSERVED, the commonest claim (a thing seen); a
    declared, authored or derived one should say so.

    A withdrawal is not built here. `Writer.withdraw` writes the terminal
    state from the instance's last one, so a retirement cannot drift from what
    was published.
    """
    rec = _blank(ENTITY)
    rec["entity_id"], rec["basis"], rec["layer"] = entity_id, basis, layer
    rec["type_uris"] = list(type_uris)
    rec["frame_ref"] = frame_ref
    if position is not None:
        rec["has_pose"], rec["pose"] = True, _pose(position, q)
    if extent is not None:
        lo, hi = [float(x) for x in extent[0]], [float(x) for x in extent[1]]
        if len(lo) != 3 or len(hi) != 3:
            raise ValueError("extent is (min_xyz, max_xyz), three values each")
        rec["has_extent"] = True
        rec["extent"] = {"min_xyz": lo, "max_xyz": hi}
    # `sequence<KV, 32>`: plain key/value pairs, in the order given. Not
    # `_meta`, which builds a manifest's namespaced MetaKV.
    rec["properties"] = [{"key": str(k), "value": str(v)}
                         for k, v in (properties or {}).items()]
    rec["state"], rec["state_reason"] = state, state_reason
    rec["source_id"] = source_id
    rec["stamp"] = stamp(when)
    _check(ENTITY, rec)
    return rec


def model_pose(entity_id: str, *, position: Sequence[float], source_id: str,
               q: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
               when: Any = None) -> Dict[str, Any]:
    """A `spatial.owm.ModelPose`: the fast lane's update to one entity's pose.

    The pose is in the entity's own frame, which the latched `Entity` states.
    A late joiner reads that and converges on the next of these.
    """
    rec = _blank(MODEL_POSE)
    rec["entity_id"], rec["source_id"] = entity_id, source_id
    rec["pose"] = _pose(position, q)
    rec["stamp"] = stamp(when)
    _check(MODEL_POSE, rec)
    return rec


def geo_anchor(anchor_id: str, *, lat: float, lon: float, alt_m: float = 0.0,
               method: str, map_id: str = "",
               sigma_enu_m: Sequence[float] = (0.0, 0.0, 0.0),
               rho_en: float = 0.0,
               q: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
               frame_ref: Optional[Dict[str, Any]] = None,
               manifest_uri: str = "", confidence: Optional[float] = None,
               when: Any = None) -> Dict[str, Any]:
    """A `spatial.core.GeoAnchor`: a pose, its method, and how sure it is.

    **Method is the claim level.** `Surveyed`, `VisualFix`, `GNSS` -- the
    record's own evidence fields are a reader's basis, and building one here
    does not upgrade a claim into surveyed truth.

    `confidence` defaults to `1/(1+sigma_max)`, the same monotone summary the
    committed anchors use, so the scalar and the matrix cannot contradict each
    other. Pass it to override, and then it is yours to justify.

    **`checksum` is a convention, not a rule.** 1.8 requires the member and
    says only "integrity/versioning"; nothing specifies what it digests
    (spatialdds-web finding 31). This one is the SHA-256 of the record's own
    canonical JSON with `checksum` itself emptied -- which a reader can
    recompute from what was served, unlike a digest of some authoring form
    they never see. If the specification settles the semantics, this changes.
    """
    rec = _blank(GEO_ANCHOR)
    s = [float(x) for x in sigma_enu_m]
    if len(s) != 3:
        raise ValueError(f"sigma_enu_m wants 3 values, got {len(s)}")
    rec["anchor_id"], rec["map_id"], rec["method"] = anchor_id, map_id, method
    rec["frame_ref"] = frame_ref or earth_fixed()
    cov = cov_pos3(s[0], s[1], s[2], rho_en=rho_en) if any(s) else cov_none()
    rec["geopose"] = geopose(lat, lon, alt_m, q=q, when=when, cov=cov)
    rec["cov"] = cov
    rec["manifest_uri"] = manifest_uri
    rec["confidence"] = (float(confidence) if confidence is not None
                         else round(1.0 / (1.0 + max(s)), 4) if any(s)
                         else 0.0)
    rec["checksum"] = ""
    rec["checksum"] = "sha256:" + hashlib.sha256(
        _canon(rec).encode()).hexdigest()
    _check(GEO_ANCHOR, rec)
    return rec


#: N.1 canonical JSON, from the module both halves share. Not a local copy:
#: the first version of this digested `json.dumps` output, which renders
#: `1.0` where N.1 renders `1`, so a reader recomputing the `checksum` with
#: the binding's own canonical form would have got a different digest.
_canon = canonjson.canon


# ---- manifests (N.7 JSON, not IDL types) -----------------------------------

#: The order the committed manifests put an asset's metadata in. Not sorted:
#: these are two keys with a conventional order in this repository's own data,
#: and a manifest rebuilt through the builders has to be the manifest that is
#: committed, byte for byte, or `gates/builders.py`'s reconstruction fails.
_META_ORDER = ("committed_path", "bytes")


def _meta(entries: Dict[str, str]) -> List[Dict[str, Any]]:
    keys = ([k for k in _META_ORDER if k in entries]
            + sorted(k for k in entries if k not in _META_ORDER))
    return [{"namespace": "spatialdds-web", "json": "",
             "entries": [{"key": k, "value": str(entries[k])} for k in keys]}]


def asset(source: Any, *, media_type: str, uri: str = "",
          meta: Optional[Dict[str, str]] = None,
          committed_path: Optional[str] = None) -> Dict[str, Any]:
    """One asset, hashed from its own bytes.

    `source` is a path or the bytes themselves. The digest and the byte count
    are **computed here, from the data**, never passed in: a hash a publisher
    typed is a hash nobody checked, and the whole point of the field is that a
    reader can verify it. `uri` is where those bytes will be reachable, which
    is the one thing the bytes cannot tell you.

    The byte count travels as metadata because readers find it useful and
    nothing in the manifest profile carries it; `committed_path` is this
    repository's own convention for saying which file in a checkout the bytes
    came from, and is why the committed manifests carry both.
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        name = ""
    else:
        path = Path(source)
        data = path.read_bytes()
        name = path.name
    entries = dict(meta or {})
    entries.setdefault("bytes", str(len(data)))
    if committed_path:
        entries.setdefault("committed_path", committed_path)
    return {"uri": uri or name,
            "media_type": media_type,
            "hash": "sha256:" + hashlib.sha256(data).hexdigest(),
            "meta": _meta(entries)}


def asset_ref(url: str, *, media_type: str, hash: str = "",
              meta: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """One asset hosted somewhere else, with whatever integrity you can state.

    `hash` comes from whoever has the bytes, because this function does not:
    pointing at a URL is not possessing what is behind it. Leave it out and
    the reference carries no integrity claim, which costs a reader exactly
    one thing -- `fetch_asset()` raises `UnverifiableIntegrity` rather than
    reporting a check it could not perform (spatialdds-web finding 31). That
    is the honest outcome, not a failure; `sd.asset()` is the form to use when
    you do have the bytes.
    """
    out: Dict[str, Any] = {"uri": url, "media_type": media_type}
    if hash:
        out["hash"] = hash if hash.startswith("sha256:") else f"sha256:{hash}"
    if meta:
        out["meta"] = _meta(meta)
    return out


def _manifest_coverage(coverage: Any,
                       cells_: Optional[Iterable[str]] = None
                       ) -> Optional[Dict[str, Any]]:
    """The coverage shape an N.7 manifest carries, which is not the IDL's.

    A manifest is a JSON document rather than a typed record, and the
    committed manifests show the difference: no `has_crs`, no coverage window,
    and the service manifest declares `geohash` cells where the content ones
    do not. `cells_` is therefore passed rather than derived -- both shapes
    are in the committed data, so a default would make one of them wrong.
    """
    cov = _coverage(coverage)
    if not cov:
        return None
    c = cov[0]
    out: Dict[str, Any] = {"frame_ref": {"uuid": "", "fqn": "earth-fixed"},
                           "has_bbox": False, "global": False,
                           "has_circle": False}
    if c.get("has_frame_ref") and c.get("frame_ref"):
        fr = c["frame_ref"]
        out["frame_ref"] = {"uuid": fr.get("uuid", ""),
                            "fqn": fr.get("fqn", "")}
    if c.get("has_circle"):
        out["has_circle"] = True
        out["circle_center"] = list(c["circle_center"])
        out["circle_radius_m"] = c["circle_radius_m"]
    elif c.get("has_bbox"):
        out["has_bbox"] = True
        out["bbox"] = list(c["bbox"])
    if cells_:
        out["geohash"] = list(cells_)
    return out


def service_manifest(service_id: str, kind: str, *, authority: str,
                     scene: str, name: str = "", org: str = "",
                     version: str = "",
                     profiles: Iterable[str] = (),
                     request: Optional[Dict[str, Any]] = None,
                     response: Optional[Dict[str, Any]] = None,
                     limits: Optional[Dict[str, Any]] = None,
                     auth: Optional[Dict[str, Any]] = None,
                     coverage: Any = None,
                     cells: Optional[Iterable[str]] = None,
                     ttl_sec: int = 1800,
                     when: Any = None) -> Dict[str, Any]:
    """An N.7 service manifest: the contract behind an announce.

    The announce says a service is here and where; this says what it accepts,
    what it answers with, and under what limits. Everything in it is a claim,
    so everything in it is an argument: `profiles` is the 1.8 profile names
    the service implements, `request` and `response` are the topic roles it
    reads and writes, `limits` is what it will refuse beyond, `auth` is how to
    present a credential. Pass none of them and the manifest says only that
    the service exists, which is a true and useless document -- publish it
    anyway if that is all you can honestly say.

    `cells` is a geohash list for the coverage; the committed service manifest
    carries one and the content manifests do not, so it is passed rather than
    derived. `sd.cells(coverage)` computes it.
    """
    doc: Dict[str, Any] = {
        "id": service_uri(authority, scene, service_id),
        "profile": MANIFEST_PROFILE,
        "rtype": "service",
        "service": {"service_id": service_id, "kind": kind,
                    "name": name or service_id, "org": org,
                    "version": version},
    }
    if profiles:
        doc["profiles"] = list(profiles)
    for key, value in (("request", request), ("response", response),
                       ("limits", limits), ("auth", auth)):
        if value:
            doc[key] = dict(value)
    cov = _manifest_coverage(coverage, cells)
    if cov:
        doc["coverage"] = cov
    doc["stamp"] = stamp(when)
    doc["ttl_sec"] = int(ttl_sec)
    return doc


def content_manifest(content_id: str, *, authority: str, scene: str,
                     assets: Iterable[Dict[str, Any]] = (),
                     title: str = "", summary: str = "",
                     tags: Iterable[str] = (), class_id: str = "",
                     coverage: Any = None,
                     cells: Optional[Iterable[str]] = None,
                     ttl_sec: int = 3600,
                     when: Any = None) -> Dict[str, Any]:
    """An N.7 content manifest, keyed by the URI it answers to.

    A manifest is a JSON document rather than an IDL type, so there is no
    generated schema to validate against and no required-member rule: this
    builds the shape the committed manifests use, which is what the resolver
    serves and what `gates/content_resolution.py` follows to bytes.
    """
    doc: Dict[str, Any] = {
        "id": content_uri(authority, scene, content_id),
        "profile": MANIFEST_PROFILE,
        "rtype": "content",
        "content": {"content_id": content_id,
                    "title": title or content_id,
                    "summary": summary, "tags": list(tags),
                    "class_id": class_id},
        "assets": list(assets),
        "stamp": stamp(when),
        "ttl_sec": int(ttl_sec),
    }
    cov = _manifest_coverage(coverage, cells)
    if cov:
        doc["coverage"] = cov
    return doc
