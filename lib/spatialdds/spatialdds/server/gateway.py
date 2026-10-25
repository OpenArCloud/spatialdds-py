"""Gateway state, built from a typed SpatialDDS MCAP.

The recording is the interface. This reads an MCAP, builds a wire model from
the IDL embedded in its schema records, decodes every message, projects each to
the binding's JSON, and indexes the result as web resources. Nothing here knows
what produced the recording.

**What a recording tells us, and what it does not.** It carries the types, the
field layout, the `@key` of every keyed type, the topic names and the message
order. All of that is read rather than configured: the key of a latched
resource is the member the IDL marks `@key`, not a name this file knows.

It does **not** carry QoS. There is nothing in an MCAP that says a topic was
TRANSIENT_LOCAL, and the binding's whole shape depends on that split: latched
state is a GET resource, a stream is a WebSocket. So durability is declared
here, per type, from the specification's QoS table rather than inferred from
the file. That is a real gap in what a recording can describe, and it is
written up as a finding.

**Access classes.** One rule, and it is worth stating plainly: *configuration
is public, observation is operator-scoped.* Zones, crossing lines, frames and
georeferences are a site's drawn layout, cacheable by anyone. Entities, tracks,
events and detections are observations of people in that site, so they are
bearer-scoped and never cacheable. Declared per type in `DURABILITY`, so the
split cannot drift from what the headers say.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from mcap.reader import make_reader

from . import commands, idlmodel, mcapmeta, pin, webjson, xcdr2

PUBLIC = "public-cacheable"
OPERATOR = "operator-scoped"
PUBLIC_MAX_AGE = 300

SCHEME = "spatialdds://"
#: A dispose, carried as MCAP metadata because 1.8 has no record for one (see
#: `publish`, which journals them). A recording made by the native layer's tap
#: carries them too, ordered against its messages by `seq`, which shares the
#: log-time axis, and `build_state` applies them.
DISPOSE_RECORD = mcapmeta.DISPOSE_RECORD
# Advertised in the resolver metadata rather than constructed by a client.
# §7.5.2 resolves through `https_base`, so this path is this endpoint's choice
# and no client may assume it.
RESOLVE_PATH = "/spatialdds/resolve"

# type FQN -> (latched, access class). Durability comes from the
# specification's QoS table; a recording does not carry it. A type absent here
# is served as a stream with the cautious class, because an unknown
# observation is still an observation.
DURABILITY: dict[str, tuple[bool, str]] = {
    "spatial.events.SpatialZone":        (True,  PUBLIC),
    "spatial.events.CrossingLine":       (True,  PUBLIC),
    "spatial.core.GeoAnchor":            (True,  PUBLIC),
    "spatial.core.FrameTransform":       (True,  PUBLIC),
    "spatial.disco.Announce":            (True,  PUBLIC),
    # A content announce is discovery data: latched, and public like the
    # site configuration it sits beside. It names content and where it is,
    # not who looked at it.
    "spatial.disco.ContentAnnounce":     (True,  PUBLIC),
    "spatial.owm.Entity":                (True,  OPERATOR),
    "spatial.semantics.FusedTrackSet":   (False, OPERATOR),
    "spatial.semantics.Detection2DSet":  (False, OPERATOR),
    "spatial.semantics.Detection3DSet":  (False, OPERATOR),
    "spatial.events.SpatialEvent":       (False, OPERATOR),
    "spatial.owm.ModelPose":             (False, OPERATOR),
}


#: Where N.3 roots a four-segment topic (`spatialdds/<stream>/<type>/<version>`,
#: no `<domain>`): the well-known namespace, as the 1.8 stamp states it.
WELL_KNOWN_ROOT = "/.well-known/spatialdds"


def resource_path(scene: str, stream: str, type_seg: str, version: str) -> str:
    """The N.3 path of a topic: domain-scoped, or well-known when it has no scene.

    One function, because the loader, the write surface and the HTTP router
    all name the same resource and must agree on its path.
    """
    root = f"/{scene}" if scene else WELL_KNOWN_ROOT
    return f"{root}/{stream}/{type_seg}/{version}"


@dataclass
class Resource:
    """One topic as a web resource, named by its §3.3.1 segments.

    `scene` is "" for a four-segment topic, which has no domain and is served
    in the well-known namespace rather than under a scene.
    """
    scene: str
    stream: str
    type_seg: str
    version: str
    topic: str
    fqn: str
    key: str
    access: str
    latched: bool

    @property
    def path(self) -> str:
        return resource_path(self.scene, self.stream, self.type_seg,
                             self.version)


@dataclass
class Instance:
    key: str
    data: dict
    etag: str


@dataclass
class State:
    """Everything the gateway serves.

    Two latched sets at two instants, and the difference is deliberate.
    `latched` is the set as of the END of the recorded window and is what a GET
    returns: the gateway's knowledge of the recording is complete, so its
    current state is the state the recording finished in.
    `latched_initial` is the set as of the START and is what a WebSocket
    subscriber replays before the marker, because a subscriber is watching the
    window play out. Replaying the final set and then streaming the history
    that produced it would hand a client the ending before the middle.
    """
    resources: dict[str, Resource] = field(default_factory=dict)
    latched: dict[str, dict[str, Instance]] = field(default_factory=dict)
    latched_initial: dict[str, dict[str, Instance]] = field(default_factory=dict)
    live: dict[str, list[dict]] = field(default_factory=dict)
    scenes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    source: str = ""
    source_sha256: str = ""
    model: idlmodel.Model | None = None
    #: Where the types came from, in words, for the banner and for anyone
    #: asking what a registry believes. A recording describes its own types;
    #: an empty registry's come from the vendored pin. Either way it is
    #: stated rather than inferred, because "what does this endpoint think a
    #: GeoAnchor is" has exactly one answer and it should be printable.
    type_source: str = ""
    # Content manifests this endpoint holds, keyed by their own full
    # `spatialdds://` URI. Keyed by the whole URI and never by a segment of
    # it: a construction rule nobody published is a dialect, which is what
    # the retired resolution fixture was.
    manifests: dict[str, dict] = field(default_factory=dict)
    # The IDL text the types were read from, kept per type rather than
    # discarded after parsing. A journal of published records has to embed the
    # same self-description a recording does -- "a backup is a recording" is
    # only true if any SpatialDDS tool can read it -- and the honest text to
    # embed is the one this gateway itself learned the type from, byte for
    # byte, not a re-flattened copy from somewhere else.
    idl: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    def resource_for(self, path: str) -> Resource | None:
        return self.resources.get(path.rstrip("/"))


def wire_model(path: Path) -> idlmodel.Model:
    """The wire model, from the IDL the recording carries for its own types."""
    return _wire_model(path)[0]


def _wire_model(path: Path) -> tuple[idlmodel.Model, dict[str, tuple[str, bytes]]]:
    """The model, and the schema record each type was defined by.

    The second return value is what lets a published record be journalled: an
    MCAP schema record needs a name and the IDL bytes, and the truthful bytes
    are the ones this file carried.
    """
    # Every record, not the digest-deduped set. Two closures can flatten to
    # byte-identical text -- `Announce` and `ContentAnnounce` do, sharing the
    # same three files -- and deduping would leave one of them registered
    # under the other's name in any journal written from this model.
    records: list[tuple[str, bytes]] = []
    with open(path, "rb") as f:
        for _sid, sch in sorted(make_reader(f).get_summary().schemas.items()):
            if sch.encoding == "omgidl":
                records.append((sch.name, sch.data))
    # The parse itself lives in `pin`, which also builds a model from the
    # vendored IDL when there is no recording. One implementation, so a
    # seeded gateway and a fresh one cannot disagree about what a type is.
    return pin.model_from_records(records, whence=str(path))


def split_topic(topic: str) -> tuple[str, str, str, str] | None:
    """`(domain, stream, type, version)` for a topic, `domain` "" when it has none.

    §3.3.1's five segments, `spatialdds/<domain>/<stream>/<type>/<version>`,
    and the four-segment names §3.3.1 also allows for well-known topics --
    `spatialdds/discovery/announce/v1` among them. Until 0.7.0 this required
    five, and a four-segment topic was dropped: measured by the spec's
    conformance suite against this gateway, an OpenVPS session served no
    resources at all (`web-binding/conformance/four_segment_probe.py`, v1.8.2).
    """
    parts = topic.split("/")
    if parts[0] != "spatialdds" or not all(parts[1:]):
        return None
    if len(parts) == 5:
        return parts[1], parts[2], parts[3], parts[4]
    if len(parts) == 4:
        return "", parts[1], parts[2], parts[3]
    return None


def uri_authority(uri: str) -> str:
    """The authority of a `spatialdds://` URI, or "" if it is not one.

    §7.5.2 makes the authority a DNS name the client resolves against, so this
    is the one part of a URI an endpoint may compare against itself.
    """
    if not uri.startswith(SCHEME):
        return ""
    rest = uri[len(SCHEME):]
    return rest.split("/", 1)[0] if "/" in rest else ""


def uri_scene(uri: str) -> str:
    """The scene a `spatialdds://<authority>/<scene>/<kind>/<id>` URI names.

    The second segment, aligned with the first segment of every resource path,
    so one lane rule covers both the collections and the resolve surface. ""
    for a URI with no scene segment, which no lane grants.
    """
    if not uri.startswith(SCHEME):
        return ""
    parts = uri[len(SCHEME):].split("/")
    return parts[1] if len(parts) > 1 else ""


def base_authority(base: str) -> str:
    """The authority this endpoint answers as, from the URL it is reached at.

    Taken from the public base rather than from the bound socket, because the
    authority in a URI is the name a client resolves and not the interface a
    process happens to have bound. `--public-url` is what makes the two agree.
    """
    return base.split("//", 1)[-1].split("/", 1)[0] if "//" in base else ""


def load_manifests(d: Path) -> dict[str, dict]:
    """Every manifest in a directory, keyed by the URI it declares as its own.

    The document's `id` is the key. Nothing is derived from the filename: a
    manifest whose `id` disagrees with where it sits is a defect in the data
    and is refused here rather than served under a name it does not claim.
    """
    out: dict[str, dict] = {}
    for f in sorted(d.glob("*.json")):
        doc = json.loads(f.read_text())
        uri = doc.get("id", "")
        if not uri_authority(uri):
            raise ValueError(
                f"{f.name}: no resolvable `id`; a manifest must declare the "
                f"full spatialdds:// URI it answers to, got {uri!r}")
        if uri in out:
            raise ValueError(f"{f.name}: {uri} is already held")
        out[uri] = doc
    return out


def resolver_metadata(st: State, base: str) -> dict | None:
    """§7.5.2 resolver metadata, or None if this endpoint is not an authority.

    An endpoint is an authority for exactly the URIs it holds manifests for.
    Holding none, or holding only manifests belonging to someone else's
    namespace, makes it a non-authority, and N.2 makes a 404 conformant there.
    Answering for a namespace it does not own would be the same fabrication
    N.2 forbids for `domain_id`, in a different costume.
    """
    mine = base_authority(base)
    if not mine or not any(uri_authority(u) == mine for u in st.manifests):
        return None
    return {"authority": mine,
            "https_base": f"{base.rstrip('/')}{RESOLVE_PATH}",
            "cache_ttl_sec": PUBLIC_MAX_AGE}


def resolve_uri(st: State, base: str, uri: str) -> dict | None:
    """The manifest for a URI this endpoint is the authority for, or None."""
    if uri_authority(uri) != base_authority(base):
        return None
    return st.manifests.get(uri)


#: Sentinel for "the IDL pinned in this package", resolved when
#: `build_state` is called rather than when it is defined. A default of
#: `pin.IDL_DIR` would be bound at import time, which makes repointing
#: `pin.IDL_DIR` -- the only honest way to ask what happens when the vendored
#: artifacts are missing -- do nothing at all. `gates/fresh_start.py` probes
#: exactly that, and spent one run serving forever because the probe could
#: not take effect.
PINNED = object()


class RecordingRefused(ValueError):
    """A recording this gateway will not serve, naming the sample that decided it.

    N.1's JSON has no form for NaN or an infinity, and DDS carries both. A
    recording holding one is therefore not servable as canonical JSON, so it
    is refused whole rather than served without the sample: a gateway that
    quietly drops what it cannot say serves a world that never existed. The
    refusal names the topic, the instance key and the member, so whoever holds
    the recording can find the offender without a decoder (finding 64).
    """

    def __init__(self, source: str, topic: str, key: str, member: str,
                 value: float, log_time: int):
        self.source, self.topic, self.key = source, topic, key
        self.member, self.value, self.log_time = member, value, log_time
        super().__init__(
            f"{source}: refused. Topic {topic}, key {key!r}, member {member} "
            f"is {value!r} at log time {log_time}. N.1's JSON has no form for "
            f"a non-finite double, so this recording cannot be served as "
            f"canonical JSON. Recapture it with a finite value, or remove the "
            f"sample.")


def _non_finite(v: Any, at: str = "") -> tuple[str, float] | None:
    """The first non-finite double in a projected document, as (member path, value)."""
    if isinstance(v, float):
        return None if math.isfinite(v) else (at, v)
    if isinstance(v, dict):
        items: Iterable = ((f"{at}.{k}" if at else str(k), x)
                           for k, x in v.items())
    elif isinstance(v, list):
        items = ((f"{at}[{i}]", x) for i, x in enumerate(v))
    else:
        return None
    for path, x in items:
        hit = _non_finite(x, path)
        if hit is not None:
            return hit
    return None


def build_state(path: Path | None, manifests: Path | None = None,
                idl_dir: Path | None | object = PINNED) -> State:
    """The state a recording describes, or an empty registry if given none.

    A seed is optional because the publish surface makes the recording one
    producer among others rather than the only one.

    With no seed the types come from the vendored IDL pin rather than from
    nowhere: an empty registry is the normal way to start one, and it has to
    know what a GeoAnchor is before anybody publishes one. `idl_dir=None`, or
    a directory without the IDL in it, leaves the gateway typeless -- which is
    still a legitimate read-only state, and the refusal that follows belongs
    where publishing is switched on.
    """
    if path is None:
        where = pin.IDL_DIR if idl_dir is PINNED else idl_dir
        model, idl = pin.wire_model(where) if where is not None \
            else (None, {})
        return State(source="", model=model, idl=idl,
                     type_source=pin.describe(where) if where is not None
                     else "none: no type source given",
                     manifests=load_manifests(manifests)
                     if manifests else {})
    model, idl = _wire_model(path)
    tables = webjson.tables()
    dec = xcdr2.Decoder(model)
    proj = webjson.Projector(model, tables)

    st = State(source=str(path), model=model, idl=idl,
               source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               type_source=f"the recording's own embedded IDL ({path.name})",
               manifests=load_manifests(manifests) if manifests else {})
    # Which latched instances were in force when the window opened. A
    # recording does not carry QoS, so "the latched set at t0" is not stated
    # anywhere in the file; what it does carry is log times, and a definition
    # published once at the start of the window is exactly an instance whose
    # first sample shares the earliest log time on its topic. Instances that
    # appear later -- a tracked person arriving -- are live traffic, not
    # latched state a late joiner at t0 would have received. Documented as an
    # inference rather than a reading.
    first_seen: dict[tuple[str, str], int] = {}
    topic_first: dict[str, int] = {}
    initial_data: dict[tuple[str, str], dict] = {}

    # Disposes the recording carries as metadata, in log-time order, applied
    # as the message stream passes them. A recording with none (a capture,
    # the fountain) is read exactly as before.
    with open(path, "rb") as f:
        disposes = sorted(
            (int(md.metadata["seq"]), md.metadata["topic"],
             md.metadata["key"], md.metadata.get("reason", ""))
            for md in make_reader(f).iter_metadata()
            if md.name == DISPOSE_RECORD)

    def apply_disposes(until: int | None) -> None:
        while disposes and (until is None or disposes[0][0] < until):
            _seq, topic, key, reason = disposes.pop(0)
            events = st.live.get(topic)
            if events is None:
                continue
            last = next((e for e in reversed(events) if e.get("key") == key),
                        None)
            # A dispose derived from a RETIRED terminal sample (below) and the
            # recorded one are the same removal: one event, not two.
            if last is not None and last["event"] == "dispose":
                if reason and not last.get("reason"):
                    last["reason"] = reason
                continue
            events.append({"event": "dispose", "topic": topic, "key": key,
                           "reason": reason or "withdrawn"})
            st.latched.get(topic, {}).pop(key, None)

    with open(path, "rb") as f:
        for sch, ch, msg in make_reader(f).iter_messages():
            apply_disposes(msg.log_time)
            fqn = sch.name.replace("::", ".")
            seg = split_topic(ch.topic)
            if seg is None:
                st.counts[f"unnamed:{ch.topic}"] = \
                    st.counts.get(f"unnamed:{ch.topic}", 0) + 1
                continue
            scene, stream, type_seg, version = seg
            res = st.resources.get(resource_path(scene, stream, type_seg, version))
            if res is None:
                agg = model.get(fqn)
                keys = [m.name for m in (agg.members if agg else [])
                        if m.is_key]
                latched, access = DURABILITY.get(fqn, (False, OPERATOR))
                res = Resource(scene, stream, type_seg, version, ch.topic, fqn,
                               keys[0] if keys else "", access, latched)
                st.resources[res.path] = res
                st.latched.setdefault(ch.topic, {})
                st.live.setdefault(ch.topic, [])
            data = proj.project(fqn, dec.decode(fqn, msg.data))
            lt = msg.log_time
            key = str(data.get(res.key, "")) if res.key else ""
            # Every sample, not only the latched ones whose digest would have
            # tripped on it: a stream sample would otherwise reach the wire as
            # Python's `NaN` token, which no JSON parser is obliged to read.
            bad = _non_finite(data)
            if bad is not None:
                raise RecordingRefused(path.name, ch.topic, key, bad[0],
                                       bad[1], lt)
            if res.latched:
                st.latched[ch.topic][key] = Instance(
                    key, data, webjson.digest(data))
                first_seen.setdefault((ch.topic, key), lt)
                topic_first[ch.topic] = min(topic_first.get(ch.topic, lt), lt)
                if (ch.topic, key) not in initial_data:
                    initial_data[(ch.topic, key)] = data
            st.live[ch.topic].append(
                {"event": "sample", "topic": ch.topic, "key": key,
                 "data": data})
            st.counts[ch.topic] = st.counts.get(ch.topic, 0) + 1

            # §2.14 removal: the terminal sample, then a dispose for the same
            # key. A lifecycle state of RETIRED is that terminal sample, so
            # the dispose is derived from the sample rather than from a signal
            # the recording does not carry.
            if data.get("state") == "RETIRED":
                st.live[ch.topic].append(
                    {"event": "dispose", "topic": ch.topic, "key": key,
                     "reason": data.get("state_reason") or "retired"})
                st.latched[ch.topic].pop(key, None)

    apply_disposes(None)
    # A four-segment topic has no scene, and "" is not a lane.
    st.scenes = sorted({r.scene for r in st.resources.values() if r.scene})

    for topic, res in ((r.topic, r) for r in st.resources.values()):
        if not res.latched:
            continue
        t0 = topic_first.get(topic)
        st.latched_initial[topic] = {
            k: Instance(k, initial_data[(topic, k)],
                        webjson.digest(initial_data[(topic, k)]))
            for (tp, k), when in first_seen.items()
            if tp == topic and when == t0}
        # Latched definitions are state, not traffic: a subscriber replays
        # them before the marker rather than receiving them again after it.
        keep = []
        emitted: set[str] = set()
        for ev in st.live[topic]:
            if (ev["event"] == "sample"
                    and ev["key"] in st.latched_initial[topic]
                    and ev["key"] not in emitted):
                emitted.add(ev["key"])
                continue
            keep.append(ev)
        st.live[topic] = keep
    return st


# ---- the envelope ----------------------------------------------------------

def replay_messages(st: State, topic: str) -> list[dict]:
    return [{"event": "sample", "topic": topic, "key": i.key, "data": i.data}
            for i in st.latched_initial.get(topic, {}).values()]


def end_of_replay(topic: str, n: int) -> dict:
    """Exactly the pinned shape. No other fields."""
    return {"event": "end_of_replay", "topic": topic, "replayed": n}


def liveliness(topic: str, key: str, alive: bool) -> dict:
    return {"event": "liveliness", "topic": topic, "key": key, "alive": alive}


# ---- geohash ---------------------------------------------------------------
# §3.3.0's search binding takes a geohash and expands it to a bounding box, so
# the gateway needs the expansion. A fixed published encoding, twenty lines, so
# it is implemented rather than taken as a dependency.

_GEOHASH32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_bbox(gh: str) -> tuple[float, float, float, float]:
    """A geohash cell as `(west, south, east, north)`, the §3.3.0 bbox order."""
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    even = True
    for ch in gh.lower():
        idx = _GEOHASH32.find(ch)
        if idx < 0:
            raise ValueError(f"not a geohash character: {ch!r}")
        for mask in (16, 8, 4, 2, 1):
            if even:
                mid = (lon_lo + lon_hi) / 2
                lon_lo, lon_hi = (mid, lon_hi) if idx & mask else (lon_lo, mid)
            else:
                mid = (lat_lo + lat_hi) / 2
                lat_lo, lat_hi = (mid, lat_hi) if idx & mask else (lat_lo, mid)
            even = not even
    return lon_lo, lat_lo, lon_hi, lat_hi


def geohash_encode(lat: float, lon: float, precision: int = 7) -> str:
    """The inverse of `geohash_bbox`, for finding the cell a position falls in.

    Needed by anything that has coordinates and wants the query that would
    return them: the gates, and a client working out which cell to ask about.
    """
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    out: list[str] = []
    bit = idx = 0
    even = True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon > mid:
                idx = idx * 2 + 1
                lon_lo = mid
            else:
                idx *= 2
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat > mid:
                idx = idx * 2 + 1
                lat_lo = mid
            else:
                idx *= 2
                lat_hi = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_GEOHASH32[idx])
            bit = idx = 0
    return "".join(out)


def intersects(a: tuple[float, float, float, float],
               b: tuple[float, float, float, float]) -> bool:
    """The §3.3.0 *intersects* predicate, as `CoverageQuery` uses it."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# ---- discovery -------------------------------------------------------------

def scene_bbox(st: State, scene: str) -> tuple[float, float, float, float] | None:
    """A scene's earth-fixed footprint, from a `GeoAnchor` in the recording.

    The box encloses every `GeoAnchor` the scene carries. Earlier this took the
    first anchor it found and returned a degenerate point, which under-reported
    a scene holding several: a search would match the one anchor's cell and miss
    the rest of the site.

    A recording with no anchors has no earth-fixed footprint and this returns
    None rather than inventing one, so a geohash query matches nothing. A
    service that does not know where it is should publish no coverage, not a
    coverage of everywhere. One anchor still gives a point box, which is
    honest: one anchor is one place.

    `GeoAnchor.geopose` is where the position lives; the fallback to a
    top-level `lat_deg` is for a flattened shape some producers emit.
    """
    lats: list[float] = []
    lons: list[float] = []
    for res in st.resources.values():
        if res.scene != scene or res.fqn != "spatial.core.GeoAnchor":
            continue
        for inst in st.latched.get(res.topic, {}).values():
            g = inst.data.get("geopose") or {}
            lat = g.get("lat_deg", inst.data.get("lat_deg"))
            lon = g.get("lon_deg", inst.data.get("lon_deg"))
            if lat is None or lon is None:
                continue
            lats.append(float(lat))
            lons.append(float(lon))
    if not lats:
        return None
    # Enclose every anchor, not the first one found. A single anchor still
    # yields a degenerate point box, which is honest: one anchor is one place.
    return min(lons), min(lats), max(lons), max(lats)


def coverage_bbox(cov: dict) -> tuple[float, float, float, float] | None:
    """A coverage element as a bbox, deriving one from a circle if need be.

    §3.3.4: "For intersects evaluation a circle MAY be approximated by its
    bounding box." Producers are told to prefer the circle so a consumer can
    recover the exact footprint; evaluating overlap against the enclosing box
    is the permitted approximation, and it errs by over-reporting, which is the
    safe direction for a search hint.
    """
    if cov.get("has_bbox") and cov.get("bbox"):
        b = cov["bbox"]
        return float(b[0]), float(b[1]), float(b[2]), float(b[3])
    if cov.get("has_circle"):
        lon, lat, _alt = cov["circle_center"]
        r = float(cov["circle_radius_m"])
        dlat = r / 111320.0
        dlon = r / max(111320.0 * math.cos(math.radians(float(lat))), 1e-9)
        return (float(lon) - dlon, float(lat) - dlat,
                float(lon) + dlon, float(lat) + dlat)
    return None


def _scene_announces(st: State, scene: str) -> list:
    """The `Announce` instances in force for a scene, wherever they were published.

    On a scene's own topic (`spatialdds/<scene>/discovery/announce/v1`, the
    web binding's convention), or on the spec's well-known four-segment
    `spatialdds/discovery/announce/v1`. That topic has no scene segment, so an
    announce there belongs to the scene its own `manifest_uri` names. Without
    that rule a native participant's announce was served at its well-known
    path and invisible to its scene's search results.
    """
    out = []
    for _path, res in sorted(st.resources.items()):
        if res.fqn != "spatial.disco.Announce":
            continue
        for inst in st.latched.get(res.topic, {}).values():
            if res.scene == scene or (
                    not res.scene
                    and uri_scene(inst.data.get("manifest_uri", "")) == scene):
                out.append(inst)
    return out


def declared_coverage(st: State, scene: str) -> list[dict] | None:
    """The coverage a recorded `Announce` declares for this scene, if any.

    **Declared beats derived.** An announce in the recording is the producer
    saying what it covers; coverage computed from the resources the gateway
    happens to serve is an inference about the same thing. When the producer
    has spoken, serve what it said.
    """
    for inst in _scene_announces(st, scene):
        cov = inst.data.get("coverage") or []
        if cov:
            return cov
    return None


def declared_services(st: State, scene: str) -> list[dict]:
    """Every `Announce` in force for a scene, recorded or published.

    A list rather than one, because the publish surface makes more than one
    possible: a second service announced into a scene the recording already
    describes is a real registry entry, and returning only the first would
    make it invisible to search while serving it perfectly over GET.
    """
    return [i.data for i in _scene_announces(st, scene)]


def declared_service(st: State, scene: str) -> dict | None:
    """The identity fields a recorded `Announce` declares, if any."""
    found = declared_services(st, scene)
    return found[0] if found else None


def service_manifest(st: State, scene: str, base: str,
                     declared: dict | None = None,
                     accepts_commands: Callable[[str], bool] | None = None
                     ) -> dict:
    """The §8.2.3 service manifest for one scene.

    `service.connection` is deliberately absent: it carries DDS connection
    hints and there is no DDS domain behind this endpoint. §8.2.3 makes the
    block optional, so omitting it is schema-legal; §3.3.0's rule that a client
    MUST be able to extract it and join a domain cannot hold for a web-native
    service, which is a reported finding rather than something to paper over
    with a fabricated domain id.

    Each topic entry carries its own `url`. `TopicMeta` has no URL field, so
    that is an addition, and it is the one that turns discovery into hypermedia:
    a client follows a link instead of rebuilding a path by string surgery on a
    topic name.
    """
    topics_block = []
    for path, res in sorted(st.resources.items()):
        if res.scene != scene:
            continue
        topics_block.append({
            "name": res.topic,
            "type": res.type_seg,
            "version": res.version,
            # Appendix N.7: durability is its own member, carrying the DDS
            # kinds verbatim. It was briefly a `qos_profile` value, which was
            # wrong twice: `qos_profile` is a free-form profile *name* in
            # §3.3.2 (VIDEO_LIVE, POSE_RT), and the token this gateway used for
            # "not latched" appeared nowhere in the specification.
            # `qos_profile` is omitted rather than reused: this gateway has no
            # deployment profile name to put there.
            "durability": "TRANSIENT_LOCAL" if res.latched else "VOLATILE",
            "access": res.access,
            "url": base + path,
        })
        # N.6 asks a client to POST a command to the lane that accepts one,
        # and N.7's topic entry has no member that says which lane that is:
        # its table is six MUST-carry fields plus `qos_profile` and
        # `liveliness`, none of which can carry "verbs welcome here". Without
        # something, a client discovers the command surface by POSTing and
        # reading the refusal, which is discovery by trial.
        #
        # So this member is added, under this repository's own name, and
        # filed as a specification gap rather than treated as settled -- see
        # docs/WIRE.md and the 1.9 candidates entry. N.7's list is a floor
        # and not a ceiling, so an extra member is conformant; it is omitted
        # entirely rather than written `false`, so a manifest from an endpoint
        # with no verbs is byte-identical to one from before this existed.
        if accepts_commands and accepts_commands(res.fqn):
            topics_block[-1][commands.ADVERTISEMENT] = True
    man: dict[str, Any] = {
        "id": f"spatialdds://spatialdds-web.local/{scene}/service/gateway",
        "profile": "spatial.manifest/1.8",
        "rtype": "service",
        "service": {
            "service_id": f"spatialdds-web-{scene[:8]}",
            "kind": "SEMANTICS",
            "name": f"SpatialDDS web gateway, domain {scene}",
            "org": "spatialdds-web.local",
            "version": "web-binding/step5",
            "topics": topics_block,
        },
        "caps": {
            "supported_profiles": [
                {"name": "spatial.core", "major": 1, "min_minor": 8,
                 "max_minor": 8},
                {"name": "spatial.events", "major": 1, "min_minor": 8,
                 "max_minor": 8},
                {"name": "spatial.semantics", "major": 1, "min_minor": 8,
                 "max_minor": 8},
            ],
            # The provisional module is a feature, not a profile range:
            # independently versioned and exempt from the 1.x additive
            # guarantee, so claiming a range would overstate the promise.
            "features": ["spatial.owm/0.1", "web-binding/json"],
        },
    }
    # Declared beats derived. A recorded Announce states the producer's own
    # identity and footprint; synthesis is the fallback for a recording that
    # carries none. The topic list stays derived either way: the gateway knows
    # what it actually serves, and `TopicMeta` cannot carry the `durability`
    # that N.7 requires of a manifest topic entry.
    decl = declared if declared is not None else declared_service(st, scene)
    if decl:
        svc = man["service"]
        for src, dst in (("service_id", "service_id"), ("name", "name"),
                         ("kind", "kind"), ("org", "org"),
                         ("version", "version")):
            if decl.get(src):
                svc[dst] = decl[src]
        if decl.get("manifest_uri"):
            man["id"] = decl["manifest_uri"]
        caps = decl.get("caps") or {}
        if caps.get("features"):
            man["caps"]["features"] = sorted(
                set(man["caps"]["features"]) | set(caps["features"]))
    cov = (declared_coverage(st, scene) or [None])[0]
    if cov is not None:
        block = {"frame_ref": {"uuid": "", "fqn": "earth-fixed"},
                 "global": bool(cov.get("global"))}
        if cov.get("has_circle"):
            block["has_circle"] = True
            block["circle_center"] = list(cov["circle_center"])
            block["circle_radius_m"] = cov["circle_radius_m"]
            block["has_bbox"] = False
        elif cov.get("has_bbox"):
            block["has_bbox"] = True
            block["bbox"] = list(cov["bbox"])
        man["coverage"] = block
        man["service"]["coverage_declared"] = True
    else:
        bbox = scene_bbox(st, scene)
        if bbox is not None:
            man["coverage"] = {"frame_ref": {"uuid": "", "fqn": "earth-fixed"},
                               "has_bbox": True, "bbox": list(bbox),
                               "global": False}
            man["service"]["coverage_declared"] = False
    return man


def search(st: State, base: str, geohash: str | None = None,
           kinds: list[str] | None = None,
           coverage: list[dict] | None = None,
           accepts_commands: Callable[[str], bool] | None = None) -> dict:
    """The §3.3.0 search response: `{"results": [...], "next_page_token": ""}`."""
    wanted: list[tuple[float, float, float, float]] = []
    if geohash:
        wanted.append(geohash_bbox(geohash))
    for el in (coverage or []):
        if el.get("global"):
            wanted = []
            break
        if el.get("has_bbox") or el.get("bbox"):
            b = el["bbox"]
            wanted.append((float(b[0]), float(b[1]), float(b[2]), float(b[3])))
    results = []
    # One result per announce in force, and a synthesized manifest only for a
    # scene that declares none. With one announce per scene -- which is what
    # both recordings carry -- this is exactly the previous behaviour.
    manifests = []
    for scene in st.scenes:
        declared = declared_services(st, scene)
        if declared:
            manifests.extend(service_manifest(st, scene, base, d,
                                              accepts_commands)
                             for d in declared)
        else:
            manifests.append(service_manifest(st, scene, base,
                                              accepts_commands=accepts_commands))
    for man in manifests:
        if kinds and man["service"]["kind"] not in kinds:
            continue
        if wanted:
            cov = man.get("coverage")
            box = coverage_bbox(cov) if cov else None
            if box is None or not any(intersects(box, w) for w in wanted):
                continue
        results.append(man)
    return {"results": results, "next_page_token": ""}
