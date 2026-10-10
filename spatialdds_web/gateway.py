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
from typing import Any, Iterable

from mcap.reader import make_reader

from spatialdds_web import idlmodel, webjson, xcdr2

PUBLIC = "public-cacheable"
OPERATOR = "operator-scoped"
PUBLIC_MAX_AGE = 300

SCHEME = "spatialdds://"
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


@dataclass
class Resource:
    """One topic as a web resource, named by its §3.3.1 segments."""
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
        return f"/{self.scene}/{self.stream}/{self.type_seg}/{self.version}"


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
    # Content manifests this endpoint holds, keyed by their own full
    # `spatialdds://` URI. Keyed by the whole URI and never by a segment of
    # it: a construction rule nobody published is a dialect, which is what
    # the retired resolution fixture was.
    manifests: dict[str, dict] = field(default_factory=dict)

    def resource_for(self, path: str) -> Resource | None:
        return self.resources.get(path.rstrip("/"))


def wire_model(path: Path) -> idlmodel.Model:
    """The wire model, from the IDL the recording carries for its own types."""
    texts: dict[str, str] = {}
    with open(path, "rb") as f:
        for _sid, sch in sorted(make_reader(f).get_summary().schemas.items()):
            if sch.encoding != "omgidl":
                continue
            texts.setdefault(hashlib.sha256(sch.data).hexdigest(),
                             sch.data.decode())
    if not texts:
        raise SystemExit(f"{path} embeds no omgidl schemas, so its types "
                         f"cannot be read from the file itself")
    model = idlmodel.Model()
    for text in texts.values():
        m = idlmodel.parse(text)
        model.aggregates.update(m.aggregates)
        model.typedefs.update(m.typedefs)
    model._leaves = None
    return model


def split_topic(topic: str) -> tuple[str, str, str, str] | None:
    """`spatialdds/<domain>/<stream>/<type>/<version>` per §3.3.1."""
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != "spatialdds":
        return None
    return parts[1], parts[2], parts[3], parts[4]


def uri_authority(uri: str) -> str:
    """The authority of a `spatialdds://` URI, or "" if it is not one.

    §7.5.2 makes the authority a DNS name the client resolves against, so this
    is the one part of a URI an endpoint may compare against itself.
    """
    if not uri.startswith(SCHEME):
        return ""
    rest = uri[len(SCHEME):]
    return rest.split("/", 1)[0] if "/" in rest else ""


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


def build_state(path: Path, manifests: Path | None = None) -> State:
    model = wire_model(path)
    tables = json.loads((Path(__file__).resolve().parent.parent
                         / "webjson" / "serializer_tables.json").read_text())
    dec = xcdr2.Decoder(model)
    proj = webjson.Projector(model, tables)

    st = State(source=str(path), model=model,
               source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
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

    with open(path, "rb") as f:
        for sch, ch, msg in make_reader(f).iter_messages():
            fqn = sch.name.replace("::", ".")
            seg = split_topic(ch.topic)
            if seg is None:
                st.counts[f"unnamed:{ch.topic}"] = \
                    st.counts.get(f"unnamed:{ch.topic}", 0) + 1
                continue
            scene, stream, type_seg, version = seg
            res = st.resources.get(f"/{scene}/{stream}/{type_seg}/{version}")
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

    st.scenes = sorted({r.scene for r in st.resources.values()})

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


def declared_coverage(st: State, scene: str) -> list[dict] | None:
    """The coverage a recorded `Announce` declares for this scene, if any.

    **Declared beats derived.** An announce in the recording is the producer
    saying what it covers; coverage computed from the resources the gateway
    happens to serve is an inference about the same thing. When the producer
    has spoken, serve what it said.
    """
    for res in st.resources.values():
        if res.scene != scene or res.fqn != "spatial.disco.Announce":
            continue
        for inst in st.latched.get(res.topic, {}).values():
            cov = inst.data.get("coverage") or []
            if cov:
                return cov
    return None


def declared_service(st: State, scene: str) -> dict | None:
    """The identity fields a recorded `Announce` declares, if any."""
    for res in st.resources.values():
        if res.scene != scene or res.fqn != "spatial.disco.Announce":
            continue
        for inst in st.latched.get(res.topic, {}).values():
            return inst.data
    return None


def service_manifest(st: State, scene: str, base: str) -> dict:
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
    decl = declared_service(st, scene)
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
           coverage: list[dict] | None = None) -> dict:
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
    for scene in st.scenes:
        man = service_manifest(st, scene, base)
        if kinds and man["service"]["kind"] not in kinds:
            continue
        if wanted:
            cov = man.get("coverage")
            box = coverage_bbox(cov) if cov else None
            if box is None or not any(intersects(box, w) for w in wanted):
                continue
        results.append(man)
    return {"results": results, "next_page_token": ""}
