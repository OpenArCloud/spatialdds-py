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
import time as _time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import canonical, canonjson

__all__ = [
    "circle", "bbox", "earth_fixed", "frame", "stamp", "cov_pos3", "cov_none",
    "geopose", "topic", "asset", "service_announce", "content_manifest",
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
           frame_ref: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A coverage element: this much ground around a point.

    `circle_center` is `[lon, lat, alt]` -- x, y, z, the order the IDL's
    comment gives and the order every committed record uses. The argument
    order here is `lat, lon` because that is the order people say, and
    getting the two confused is the kind of mistake that puts a service in
    the ocean.
    """
    c = _blank(COVERAGE)
    c["has_crs"], c["crs"] = True, crs
    c["has_circle"] = True
    c["circle_center"] = [float(lon), float(lat), float(alt_m)]
    c["circle_radius_m"] = float(radius_m)
    if frame_ref is not None:
        c["has_frame_ref"], c["frame_ref"] = True, frame_ref
    return c


def bbox(west: float, south: float, east: float, north: float, *,
         crs: str = "EPSG:4326",
         frame_ref: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A coverage element: this box of ground. §3.3.0's own bbox order."""
    c = _blank(COVERAGE)
    c["has_crs"], c["crs"] = True, crs
    c["has_bbox"] = True
    c["bbox"] = [float(west), float(south), float(east), float(north)]
    if frame_ref is not None:
        c["has_frame_ref"], c["frame_ref"] = True, frame_ref
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


def topic(type_name: str, *, scene: str, stream: str, version: str = "v1",
          durability: str = "TRANSIENT_LOCAL", qos_profile: str = "",
          rate_hz: float = 0.0, max_chunk_bytes: int = 0) -> Dict[str, Any]:
    """One `TopicMeta`, with the §3.3.1 topic name built from its segments.

    `durability` defaults to TRANSIENT_LOCAL because the things a registry
    holds are definitions: a late joiner has to receive them or discovery does
    not work. It is a member rather than an assumption as of the 1.8 revision
    (spec 9c3e1f2), which is why it can be stated here at all.
    """
    tm = _blank(TOPIC_META)
    tm["name"] = f"spatialdds/{scene}/{stream}/{type_name}/{version}"
    tm["type"], tm["version"] = type_name, version
    tm["durability"], tm["qos_profile"] = durability, qos_profile
    tm["target_rate_hz"] = float(rate_hz)
    tm["max_chunk_bytes"] = int(max_chunk_bytes)
    return tm


# ---- URIs, assembled under a stated authority ------------------------------

def _uri(authority: str, scene: str, kind: str, ident: str) -> str:
    if not authority:
        raise ValueError(
            "a spatialdds:// URI needs an authority, and this library will "
            "not invent one: it is a DNS name somebody owns. Pass "
            "authority=, or let an Endpoint fill it from the resolver "
            "metadata the endpoint publishes about itself.")
    return f"spatialdds://{authority}/{scene}/{kind}/{ident.lstrip('/')}"


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

def asset(uri: str, media_type: str, *, sha256: str = "",
          meta: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """One entry in a manifest's `assets`, with its integrity claim.

    `sha256` is the digest of the asset's bytes, and the client's
    `fetch_asset` checks exactly that. Leave it out and a reader gets
    `UnverifiableIntegrity` rather than a check that silently did nothing.
    """
    out: Dict[str, Any] = {"uri": uri, "media_type": media_type}
    if sha256:
        out["hash"] = sha256 if sha256.startswith("sha256:") \
            else f"sha256:{sha256}"
    if meta:
        out["meta"] = [{"namespace": "spatialdds-web", "json": "",
                        "entries": [{"key": k, "value": v}
                                    for k, v in sorted(meta.items())]}]
    return out


def content_manifest(content_id: str, *, authority: str, scene: str,
                     assets: Iterable[Dict[str, Any]] = (),
                     title: str = "", summary: str = "",
                     tags: Iterable[str] = (), class_id: str = "",
                     coverage: Any = None, ttl_sec: int = 3600,
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
        "content": {"content_id": content_id, "title": title or content_id,
                    "summary": summary, "tags": list(tags),
                    "class_id": class_id},
        "assets": list(assets),
        "stamp": stamp(when),
        "ttl_sec": int(ttl_sec),
    }
    cov = _coverage(coverage)
    if cov:
        c = cov[0]
        out: Dict[str, Any] = {"frame_ref": {"uuid": "", "fqn": "earth-fixed"},
                               "has_bbox": False, "global": False,
                               "has_circle": False}
        if c.get("has_circle"):
            out["has_circle"] = True
            out["circle_center"] = list(c["circle_center"])
            out["circle_radius_m"] = c["circle_radius_m"]
        elif c.get("has_bbox"):
            out["has_bbox"] = True
            out["bbox"] = list(c["bbox"])
        doc["coverage"] = out
    return doc
