"""An MCP server over the web binding: live OWM scenes, for any MCP client.

    pip install "spatialdds[mcp]"
    spatialdds-mcp --scene https://demo.spatialdds.org/ut-austin-south-mall

Claude Desktop, Claude Code, or any agent framework that speaks the Model
Context Protocol can then ask what is in a scene, what is claimed about it,
and by whom. Five read-only tools, named here and versioned with the package:
`list_scenes`, `describe_scene`, `get_entities`, `get_claims`, `get_entity`.
The guide is docs/mcp-server.md in the repository, and every example in it is
run by `gates/mcp_guide.py`.

**The rule that keeps this small: the server is a client of the web binding
and nothing else.** It reads with `spatialdds.connect()`, the package's own
client, and watches with that client's subscriptions. It never imports DDS or
the gateway, holds no credential, and sees exactly what any stranger's `GET`
sees: a lane the binding serves only with a token (N.4's operator-scoped
class) is described as one, and not read. `gates/mcp_server.py` holds that
against the import graph and the running process.

**Who said so, and how fresh.** Every record in every result carries its
asserter (a claim's `asserter`; an entity's `source_id`, its own statement),
its basis, its stamp, and an age computed here from that stamp. A record
whose writer the binding reported gone is returned with the words "nobody
asserts this now", never dropped: what to make of it is the reader's call.
A record the binding withdrew while this server watched is returned too,
marked as no longer served, with the reason the binding gave.

**Liveliness, as the binding serves it.** Appendix N carries a writer's
liveliness only as a `liveliness` event on a lane's WebSocket; a `GET`
carries none, and a subscription that opens later is not told what was lost
before it. So this server holds one subscription per public world lane from
the moment it starts, and reports what that subscription heard: `lost` (the
binding said the writer went away, and nothing has been written since),
`reported` (a live write or an alive event since the watch began),
`unreported` (nothing either way since the watch began), or `unwatched`
(there is no subscription, and why). Replayed samples are not evidence that a
writer is alive; the latched set is exactly what a crashed writer leaves
behind. See WEB-BINDING-FINDINGS.md, finding 81.

**It never fuses, resolves, or ranks.** Two asserters' claims about one
proposition come back side by side, as two claims, in the order the binding
served them, grouped so they sit together. Consumers build their own views,
and this consumer's consumer is a model.

Out of scope, on purpose: writes (they wait on the web binding's credential
flow), MCP subscriptions, discovery of scenes near a place, and any trust or
fusion logic.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from . import __version__
from .client import Endpoint, Topic, connect
from .errors import Problem, SpatialDDSError
from .events import Dispose, EndOfReplay, Liveliness, Sample

__all__ = ["MissingExtra", "EXTRA", "TOOLS", "Catalog", "View", "build",
           "main", "RETIRED_KEPT_S", "RETIRED_KEPT_N", "SEARCH_PAGES"]

#: The install line a missing extra names.
EXTRA = 'pip install "spatialdds[mcp]"'
#: The tools, by their stable names. A rename is a breaking change.
TOOLS = ("list_scenes", "describe_scene", "get_entities", "get_claims",
         "get_entity")
#: How long, and how many per lane, a record the binding withdrew while this
#: server watched is still returned (marked as no longer served).
RETIRED_KEPT_S = 3600.0
RETIRED_KEPT_N = 256
#: Search pages followed per endpoint. The binding pages with
#: `next_page_token`; `Endpoint.search()` reads the first page only
#: (finding 82), so the pages are followed here.
SEARCH_PAGES = 20
#: The lane types whose records are a world model's: what the watches follow.
ENTITY_FQN = "spatial.owm.Entity"
CLAIM_FQN = "oarc_owm.Relationship"
WORLD_FQNS = (ENTITY_FQN, CLAIM_FQN)
#: The manifest's access class for a lane read without a token.
PUBLIC = "public-cacheable"
OBB_PREFIX = "demo.obb."
WELL_KNOWN = "/.well-known/spatialdds"


class MissingExtra(ImportError):
    """The MCP server was asked for without the `mcp` package installed."""


def require_mcp() -> None:
    """Raise `MissingExtra`, naming the extra, unless `mcp` imports."""
    try:
        import mcp.server.mcpserver  # noqa: F401
    except ImportError as e:
        raise MissingExtra(f"the MCP server needs the mcp package: {EXTRA}") \
            from e


# ---- time -----------------------------------------------------------------

def stamp_seconds(stamp: Any) -> Optional[float]:
    """A `builtin.Time` as canonical JSON (`{"sec": "…", "nanosec": n}`)."""
    if not isinstance(stamp, dict):
        return None
    try:
        return int(stamp.get("sec", 0)) + int(stamp.get("nanosec", 0)) / 1e9
    except (TypeError, ValueError):
        return None


def iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(t, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def spoken_age(age: float) -> str:
    """An age as a reader says it. Negative means stamped in the future."""
    if age < 0:
        return f"stamped {spoken_age(-age)} in the future"
    if age < 90:
        return f"{age:.0f} s"
    if age < 90 * 60:
        return f"{age / 60:.0f} min"
    if age < 36 * 3600:
        return f"{age / 3600:.1f} h"
    return f"{age / 86400:.1f} d"


def age_phrase(age: Optional[float]) -> str:
    if age is None:
        return "no stamp"
    return spoken_age(age) if age < 0 else f"{spoken_age(age)} ago"


# ---- the scenes -----------------------------------------------------------

@dataclass
class Lane:
    """One topic of a scene, as its service manifest advertised it."""
    type: str
    url: str
    access: str
    durability: str
    name: str
    meta: Dict[str, Any]

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def public(self) -> bool:
        return self.access == PUBLIC

    @property
    def latched(self) -> bool:
        return self.durability == "TRANSIENT_LOCAL"


@dataclass
class Scene:
    """A scene: one endpoint, one §3.3.1 domain segment, and its lanes."""
    key: str
    name: str
    base: str
    authority: str
    services: List[str] = field(default_factory=list)
    lanes: List[Lane] = field(default_factory=list)

    def lane_of(self, type_seg: str) -> Optional[Lane]:
        return next((l for l in self.lanes if l.type == type_seg), None)


@dataclass
class Source:
    """One `--scene` URL: an endpoint, and the one scene it names, if any."""
    url: str
    base: str
    only: Optional[str]
    ep: Endpoint


def parse_source(url: str, timeout: float = 15.0) -> Source:
    """`https://host` (every scene its search lists) or `https://host/scene`."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"--scene takes a binding base URL, http(s)://host, "
                         f"optionally with one scene: {url!r}")
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) > 1:
        raise ValueError(f"--scene names at most one scene after the host: "
                         f"{url!r}")
    base = f"{parts.scheme}://{parts.netloc}"
    # lifted=False: every record is returned as the binding served it.
    return Source(url, base, segs[0] if segs else None,
                  connect(base, lifted=False, timeout=timeout))


def search_all(ep: Endpoint) -> List[Dict[str, Any]]:
    """Every service manifest the endpoint's search lists, page by page."""
    return [h.manifest for h in ep.search(max_pages=SEARCH_PAGES)]


class Catalog:
    """The scenes the server was pointed at, read from their endpoints.

    Read on every call that needs it rather than cached: a lane can open at
    any time, and a search is one request. An endpoint that cannot be read is
    listed with the reason, never left out.
    """

    def __init__(self, urls: List[str], timeout: float = 15.0) -> None:
        if not urls:
            raise ValueError("no --scene given: name at least one binding "
                             "base URL")
        self.sources = [parse_source(u, timeout) for u in urls]

    def read(self) -> Tuple[List[Scene], List[Dict[str, str]]]:
        scenes: Dict[str, Scene] = {}
        failed: List[Dict[str, str]] = []
        for src in self.sources:
            authority = urlsplit(src.base).netloc
            try:
                manifests = search_all(src.ep)
            except SpatialDDSError as e:
                failed.append({"source": src.url, "error": str(e)})
                continue
            found = False
            for m in manifests:
                svc = m.get("service", {})
                for t in svc.get("topics", []):
                    segs = [s for s in urlsplit(t.get("url", "")).path
                            .split("/") if s]
                    if not segs or segs[0] == ".well-known":
                        continue
                    name = segs[0]
                    if src.only and name != src.only:
                        continue
                    found = True
                    key = f"{authority}/{name}"
                    sc = scenes.setdefault(key, Scene(key, name, src.base,
                                                      authority))
                    sid = svc.get("service_id", "")
                    if sid and sid not in sc.services:
                        sc.services.append(sid)
                    if sc.lane_of(t.get("type", "")) is None:
                        sc.lanes.append(Lane(t.get("type", ""),
                                             t.get("url", ""),
                                             t.get("access", ""),
                                             t.get("durability", ""),
                                             t.get("name", ""), t))
            if src.only and not found:
                failed.append({"source": src.url,
                               "error": f"the endpoint's search lists no "
                                        f"scene {src.only!r}"})
        return list(scenes.values()), failed

    def ep_for(self, sc: Scene) -> Endpoint:
        return next(s.ep for s in self.sources if s.base == sc.base)


# ---- what the watches heard -----------------------------------------------

@dataclass
class Heard:
    """Per key: the last record a watch saw, and what it says of its writer."""
    data: Optional[Dict[str, Any]] = None
    #: "lost" | "reported" | None (nothing either way since the watch began)
    liveliness: Optional[str] = None
    liveliness_at: Optional[float] = None
    #: Set when the binding withdrew it: when, and the reason it gave.
    withdrawn_at: Optional[float] = None
    reason: str = ""


class Watch:
    """One subscription to one public world lane, held for the server's life.

    A daemon thread around `Topic.subscribe()`: replay, one end-of-replay,
    then live. It reconnects after a failure, and says when it did, because
    events in the gap were not heard.
    """

    def __init__(self, ep: Endpoint, lane: Lane, fqn: str) -> None:
        self.ep, self.lane, self.fqn = ep, lane, fqn
        self.lock = threading.Lock()
        self.heard: Dict[str, Heard] = {}
        self.since: Optional[float] = None
        self.live = False
        self.events = 0
        self.gaps: List[Tuple[float, float]] = []
        self.error = ""
        self._down_at: Optional[float] = None
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"watch {lane.path}")
        self.thread.start()

    def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                for ev in Topic(self.ep, self.lane.meta).subscribe():
                    self._apply(ev)
                    delay = 1.0
                self.error = "the binding closed the subscription"
            except Problem as e:
                self.error = f"{e.status} {e.slug}: refused"
                if e.status in (401, 403, 404):
                    self._down()
                    return
            except Exception as e:                       # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
            self._down()
            time.sleep(delay)
            delay = min(delay * 2, 30.0)

    def _down(self) -> None:
        with self.lock:
            if self.live:
                self._down_at = time.time()
            self.live = False

    def _apply(self, ev: Any) -> None:
        now = time.time()
        with self.lock:
            self.events += 1
            if isinstance(ev, EndOfReplay):
                if self.since is None:
                    self.since = now
                elif self._down_at is not None:
                    self.gaps.append((self._down_at, now))
                    del self.gaps[:-8]
                self._down_at = None
                self.live, self.error = True, ""
                return
            key = getattr(ev, "key", "")
            if not key:
                return
            h = self.heard.setdefault(key, Heard())
            if isinstance(ev, Sample):
                h.data = ev.data
                if (ev.data or {}).get("state") == "RETIRED":
                    h.reason = (ev.data or {}).get("state_reason", "")
                else:
                    h.withdrawn_at, h.reason = None, ""
                if self.live:
                    # A write after the replay is a live writer saying so,
                    # a terminal one included. A replayed one is not: the
                    # latched set is exactly what a crashed writer leaves
                    # behind.
                    h.liveliness, h.liveliness_at = "reported", now
            elif isinstance(ev, Dispose):
                h.withdrawn_at = now
                h.reason = ev.reason or h.reason
            elif isinstance(ev, Liveliness) and self.live:
                h.liveliness = "reported" if ev.alive else "lost"
                h.liveliness_at = now
            self._prune(now)

    def _prune(self, now: float) -> None:
        gone = sorted((h.withdrawn_at, k) for k, h in self.heard.items()
                      if h.withdrawn_at is not None)
        old = {k for t, k in gone if now - t > RETIRED_KEPT_S}
        old |= {k for _t, k in gone[:max(0, len(gone) - RETIRED_KEPT_N)]}
        for k in old:
            self.heard.pop(k, None)

    def about(self, key: str) -> Heard:
        with self.lock:
            h = self.heard.get(key)
            return Heard(**vars(h)) if h else Heard()

    def withdrawn(self) -> List[Tuple[str, Heard]]:
        with self.lock:
            self._prune(time.time())
            return [(k, Heard(**vars(h))) for k, h in self.heard.items()
                    if h.withdrawn_at is not None and h.data is not None]

    def describe(self) -> Dict[str, Any]:
        with self.lock:
            return {"watching": self.live, "since": iso(self.since),
                    "events": self.events,
                    "gaps": [[iso(a), iso(b)] for a, b in self.gaps],
                    **({"error": self.error} if self.error else {})}


# ---- the tools ------------------------------------------------------------

class ToolFailure(Exception):
    """A tool call the caller can fix: an unknown scene, a bad argument."""


def _get(ep: Endpoint, path: str) -> Tuple[Any, Any]:
    """A GET, and the headers that say what the binding served.

    The headers stay case-insensitive: behind an HTTP/2 front they arrive
    lower-cased, and a plain dict would lose `X-SpatialDDS-Type`.
    """
    r = ep._request("GET", path, raw=True)
    return r.json(), r.headers


def _props(rec: Dict[str, Any]) -> Dict[str, str]:
    return {p.get("key", ""): p.get("value", "")
            for p in rec.get("properties") or [] if isinstance(p, dict)}


def _last_segment(uri: str) -> str:
    tail = uri.rstrip("/").rsplit("/", 1)[-1]
    return tail or uri


def _fmt(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".") if abs(x) < 1e6 else f"{x:.3g}"


def _point(v: List[float]) -> str:
    return "(" + ", ".join(_fmt(c) for c in v) + ")"


_VERB = {"DECLARED": "declares", "OBSERVED": "observes",
         "DERIVED": "derives", "AUTHORED": "authors"}


class View:
    """The five tools, as plain functions over a catalog and its watches.

    Importable and callable without the `mcp` package: `build()` is the only
    place the protocol enters, so the reading, the watching and the
    rendering are exercised by the same code whichever transport calls them.
    """

    def __init__(self, catalog: Catalog, watch: bool = True,
                 clock=time.time) -> None:
        self.catalog = catalog
        self.watch = watch
        self.clock = clock
        self.watches: Dict[str, Watch] = {}
        self._lock = threading.Lock()

    # -- plumbing --
    def scenes(self) -> Tuple[List[Scene], List[Dict[str, str]]]:
        scenes, failed = self.catalog.read()
        if self.watch:
            for sc in scenes:
                self._ensure_watches(sc)
        return scenes, failed

    def start(self) -> None:
        """Read the catalog once, so the watches begin at startup."""
        try:
            self.scenes()
        except SpatialDDSError:
            pass

    @staticmethod
    def _fqn_of(lane: Lane) -> str:
        """A world lane's type, by its §3.3.2 type segment.

        Only the two this server reads; `describe_scene` reports every
        lane's type as the binding states it (`X-SpatialDDS-Type`).
        """
        return {"entity": ENTITY_FQN,
                "oarc.relationship": CLAIM_FQN}.get(lane.type, "")

    def _ensure_watches(self, sc: Scene) -> None:
        for lane in sc.lanes:
            fqn = self._fqn_of(lane)
            if fqn not in WORLD_FQNS or not lane.public:
                continue
            key = sc.base + lane.path
            with self._lock:
                if key not in self.watches:
                    self.watches[key] = Watch(self.catalog.ep_for(sc), lane,
                                              fqn)

    def watch_of(self, sc: Scene, lane: Optional[Lane]) -> Optional[Watch]:
        if lane is None:
            return None
        with self._lock:
            return self.watches.get(sc.base + lane.path)

    def scene(self, name: str) -> Scene:
        scenes, failed = self.scenes()
        hits = [s for s in scenes if name in (s.key, s.name)]
        if len(hits) == 1:
            return hits[0]
        known = ", ".join(sorted(s.key for s in scenes)) or "none"
        if hits:
            raise ToolFailure(f"{name!r} names {len(hits)} scenes; use one "
                              f"of: {', '.join(sorted(s.key for s in hits))}")
        why = "".join(f"; {f['source']}: {f['error']}" for f in failed)
        raise ToolFailure(f"no scene {name!r}. This server's scenes: "
                          f"{known}{why}")

    def _unreadable(self, sc: Scene, lane: Optional[Lane],
                    what: str) -> Optional[Dict[str, Any]]:
        """None if the lane can be read without a token; else why not."""
        if lane is None:
            return {"readable": False,
                    "note": f"{sc.key} serves no {what} lane: its service "
                            f"manifest lists none"}
        if not lane.public:
            return {"readable": False, "lane": lane.url,
                    "access": lane.access,
                    "note": f"{sc.key}'s {what} lane is {lane.access}: the "
                            f"binding serves it only with a token, and this "
                            f"server holds none (N.4)"}
        return None

    def _liveliness(self, w: Optional[Watch], lane: Optional[Lane],
                    h: Heard) -> Dict[str, Any]:
        if w is None:
            why = ("the server was started with watching off" if not
                   self.watch else "no subscription to this lane")
            return {"liveliness": "unwatched", "liveliness_note": why}
        if h.liveliness is not None:
            return {"liveliness": h.liveliness,
                    "liveliness_at": iso(h.liveliness_at)}
        since = w.describe()["since"]
        return {"liveliness": "unreported",
                "liveliness_note": f"nothing about its writer since the "
                                   f"watch began at {since}"
                if since else "the watch has not finished its replay"}

    def _freshness(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        t = stamp_seconds(rec.get("stamp"))
        age = None if t is None else round(self.clock() - t, 1)
        return {"stamped": iso(t), "age_s": age}

    # -- entities --
    def _entity(self, sc: Scene, rec: Dict[str, Any], w: Optional[Watch],
                lane: Lane, heard: Optional[Heard] = None) -> Dict[str, Any]:
        h = heard if heard is not None else (
            w.about(rec.get("entity_id", "")) if w else Heard())
        props = _props(rec)
        obb = {k[len(OBB_PREFIX):]: v for k, v in props.items()
               if k.startswith(OBB_PREFIX)}
        out: Dict[str, Any] = {
            "entity_id": rec.get("entity_id"),
            "label": props.get("demo.label") or props.get("label"),
            "type_uris": rec.get("type_uris", []),
            "asserter": rec.get("source_id", ""),
            "asserter_from": "source_id, the entity's own statement",
            "basis": rec.get("basis"),
            "state": rec.get("state"),
            "state_reason": rec.get("state_reason", ""),
            "frame": (rec.get("frame_ref") or {}).get("fqn"),
            "pose": rec.get("pose") if rec.get("has_pose") else None,
            "extent": rec.get("extent") if rec.get("has_extent") else None,
            "stamp": rec.get("stamp"),
            **self._freshness(rec),
            **self._liveliness(w, lane, h),
            "served": h.withdrawn_at is None,
        }
        if obb:
            out["obb"] = obb
        if h.withdrawn_at is not None:
            out["withdrawn_at"] = iso(h.withdrawn_at)
            out["withdrawn_reason"] = h.reason
        out["rendering"] = self._render_entity(out)
        return out

    def _render_entity(self, e: Dict[str, Any]) -> str:
        name = e["entity_id"] + (f" ({e['label']})" if e.get("label") else "")
        verb = {"DECLARED": "declared", "OBSERVED": "observed",
                "DERIVED": "derived", "AUTHORED": "authored"}.get(
            e.get("basis") or "", str(e.get("basis")).lower())
        s = f"{name}: {verb} by {e['asserter'] or 'nobody named'}"
        if e.get("pose"):
            s += f", at {_point(e['pose'].get('t', []))} in {e['frame']}"
        if e.get("extent"):
            lo, hi = e["extent"]["min_xyz"], e["extent"]["max_xyz"]
            s += ", box " + " x ".join(_fmt(b - a) for a, b in zip(lo, hi)) \
                + " m"
        if e.get("obb"):
            dims = [e["obb"].get(k) for k in ("length_m", "width_m",
                                               "height_m")]
            if all(d is not None for d in dims):
                s += f" (oriented {' x '.join(dims)} m)"
        if e.get("state") not in (None, "ACTIVE"):
            s += f", {str(e['state']).lower()}"
            if e.get("state_reason"):
                s += f": {e['state_reason']}"
        s += f", stamped {e['stamped']} ({age_phrase(e['age_s'])})"
        if e.get("distance_m") is not None:
            s += f", {_fmt(e['distance_m'])} m from the point"
        return s + self._tail(e)

    def _tail(self, r: Dict[str, Any]) -> str:
        s = ""
        if r.get("liveliness") == "lost":
            s += (f"; nobody asserts this now (the binding reported its "
                  f"writer gone at {r['liveliness_at']})")
        if not r.get("served", True):
            s += (f"; withdrawn at {r['withdrawn_at']}, no longer served"
                  + (f": {r['withdrawn_reason']}"
                     if r.get("withdrawn_reason") else ""))
        return s

    def _entities(self, sc: Scene) -> Tuple[Optional[Dict[str, Any]],
                                             List[Dict[str, Any]]]:
        lane = sc.lane_of("entity")
        bad = self._unreadable(sc, lane, "entity")
        if bad:
            return bad, []
        ep = self.catalog.ep_for(sc)
        docs, _h = _get(ep, lane.path)
        w = self.watch_of(sc, lane)
        out = [self._entity(sc, d, w, lane) for d in docs]
        served = {d.get("entity_id") for d in docs}
        if w:
            out += [self._entity(sc, h.data, w, lane, h)
                    for k, h in w.withdrawn() if k not in served]
        return None, out

    def _near(self, rows: List[Dict[str, Any]], near: Dict[str, Any]
              ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        try:
            p = [float(near["x"]), float(near["y"])]
            if near.get("z") is not None:
                p.append(float(near["z"]))
            radius = float(near["radius_m"])
        except (KeyError, TypeError, ValueError):
            raise ToolFailure("near takes x, y, radius_m (and optionally z "
                              "and frame), numbers in the scene's frame")
        if not (radius >= 0 and all(math.isfinite(c) for c in p + [radius])):
            raise ToolFailure("near: radius_m must be a finite number >= 0")
        frames = sorted({r["frame"] for r in rows if r.get("pose")})
        frame = near.get("frame")
        if frame is None:
            if len(frames) > 1:
                raise ToolFailure(f"near: this scene's entities are in "
                                  f"{len(frames)} frames ({', '.join(frames)}"
                                  f"); name one as near.frame")
            frame = frames[0] if frames else None
        kept, unplaced, other = [], 0, 0
        for r in rows:
            if not r.get("pose"):
                unplaced += 1
                continue
            if r["frame"] != frame:
                other += 1
                continue
            d = _distance(p, r)
            if d <= radius:
                r = {**r, "distance_m": round(d, 2),
                     "measured_to": "extent" if r.get("extent") else "pose"}
                r["rendering"] = self._render_entity(r)
                kept.append(r)
        return kept, {"point": p, "radius_m": radius, "frame": frame,
                      "not_placed": unplaced, "in_other_frames": other}

    def get_entities(self, scene: str, type: Optional[str] = None,
                     near: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        sc = self.scene(scene)
        bad, rows = self._entities(sc)
        if bad:
            return {"scene": sc.key, **bad, "entities": []}
        result: Dict[str, Any] = {"scene": sc.key}
        if type:
            rows = [r for r in rows
                    if any(type in (u, _last_segment(u))
                           for u in r["type_uris"])]
            result["type"] = type
        if near is not None:
            rows, result["near"] = self._near(rows, near)
        result["entities"] = rows
        result["count"] = len(rows)
        return result

    def get_entity(self, scene: str, id: str) -> Dict[str, Any]:
        sc = self.scene(scene)
        result: Dict[str, Any] = {"scene": sc.key, "id": id}
        lane = sc.lane_of("entity")
        bad = self._unreadable(sc, lane, "entity")
        if bad:
            result["entity"] = None
            result["entity_note"] = bad["note"]
        else:
            ep = self.catalog.ep_for(sc)
            w = self.watch_of(sc, lane)
            try:
                doc = Topic(ep, lane.meta).instance(id)
                result["entity"] = self._entity(sc, doc, w, lane)
            except Problem as e:
                if e.status != 404:
                    raise
                h = w.about(id) if w else Heard()
                if h.data is not None and h.withdrawn_at is not None:
                    result["entity"] = self._entity(sc, h.data, w, lane, h)
                else:
                    result["entity"] = None
                    result["entity_note"] = (
                        f"{sc.key} serves no entity {id!r} (404)"
                        + ("" if w is None else
                           ", and none was withdrawn while this server "
                           "watched"))
        claims = self.get_claims(scene)
        mine = [c for c in claims.get("claims", [])
                if id in (c["claim"].get("subject_ref"),
                          c["claim"].get("object_ref"))]
        result["claims"] = mine
        if claims.get("readable") is False:
            result["claims_note"] = claims["note"]
        result["claim_count"] = len(mine)
        return result

    # -- claims --
    def _claim(self, rec: Dict[str, Any], w: Optional[Watch], lane: Lane,
               heard: Optional[Heard] = None) -> Dict[str, Any]:
        h = heard if heard is not None else (
            w.about(rec.get("id", "")) if w else Heard())
        out: Dict[str, Any] = {
            "claim": rec,
            "proposition": [rec.get("subject_ref"), rec.get("predicate"),
                            rec.get("object_ref")],
            **self._freshness(rec),
            **self._liveliness(w, lane, h),
            "served": h.withdrawn_at is None,
        }
        if h.withdrawn_at is not None:
            out["withdrawn_at"] = iso(h.withdrawn_at)
            out["withdrawn_reason"] = h.reason
        out["rendering"] = self._render_claim(out)
        return out

    def _render_claim(self, c: Dict[str, Any]) -> str:
        r = c["claim"]
        verb = _VERB.get(r.get("basis", ""), str(r.get("basis", "")).lower())
        s = (f"{r.get('asserter') or 'nobody named'} {verb} "
             f"{r.get('subject_ref')} {_last_segment(r.get('predicate', ''))} "
             f"{r.get('object_ref')}")
        if r.get("has_confidence"):
            # As written: a rounded confidence would misquote the claim.
            s += f", confidence {float(r.get('confidence', 0.0)):g}"
        if r.get("has_valid_from"):
            s += f", valid from {iso(stamp_seconds(r.get('valid_from')))}"
        if r.get("has_valid_until"):
            s += f", valid until {iso(stamp_seconds(r.get('valid_until')))}"
        if r.get("derived_from"):
            s += f", derived from {len(r['derived_from'])} record(s)"
        state = r.get("state")
        if state == "REVISED":
            s += ", revised"
        elif state == "RETIRED":
            s += ", retired" + (f": {r.get('state_reason')}"
                                if r.get("state_reason") else "")
        s += f", stamped {c['stamped']} ({age_phrase(c['age_s'])})"
        return s + self._tail({**c, "withdrawn_reason":
                               "" if state == "RETIRED"
                               else c.get("withdrawn_reason", "")})

    def get_claims(self, scene: str, subject: Optional[str] = None,
                   predicate: Optional[str] = None,
                   asserter: Optional[str] = None) -> Dict[str, Any]:
        sc = self.scene(scene)
        lane = sc.lane_of("oarc.relationship")
        bad = self._unreadable(sc, lane, "claim")
        result: Dict[str, Any] = {"scene": sc.key}
        filters = {k: v for k, v in (("subject", subject),
                                     ("predicate", predicate),
                                     ("asserter", asserter)) if v}
        result.update(filters)
        if bad:
            return {**result, **bad, "claims": []}
        ep = self.catalog.ep_for(sc)
        docs, _h = _get(ep, lane.path)
        w = self.watch_of(sc, lane)
        rows = [self._claim(d, w, lane) for d in docs]
        served = {d.get("id") for d in docs}
        if w:
            rows += [self._claim(h.data, w, lane, h)
                     for k, h in w.withdrawn() if k not in served]

        def keep(c: Dict[str, Any]) -> bool:
            r = c["claim"]
            return ((not subject or subject in (r.get("subject_ref"),
                                                r.get("object_ref")))
                    and (not predicate or predicate in (
                        r.get("predicate"),
                        _last_segment(r.get("predicate", ""))))
                    and (not asserter or r.get("asserter") == asserter))
        rows = [c for c in rows if keep(c)]
        # Side by side: claims about one proposition sit together, in the
        # order the binding served the first of them. Grouping, not ranking.
        order: Dict[Tuple, int] = {}
        for c in rows:
            order.setdefault(tuple(c["proposition"]), len(order))
        rows.sort(key=lambda c: order[tuple(c["proposition"])])
        result["claims"] = rows
        result["count"] = len(rows)
        return result

    # -- scenes --
    def list_scenes(self) -> Dict[str, Any]:
        scenes, failed = self.scenes()
        rows = []
        for sc in scenes:
            types = sorted({l.type for l in sc.lanes})
            row = {"scene": sc.key, "name": sc.name, "endpoint": sc.base,
                   "services": sc.services, "lanes": len(sc.lanes),
                   "types": len(types), "type_names": types,
                   "public_lanes": sum(1 for l in sc.lanes if l.public)}
            row["rendering"] = (
                f"{sc.key}: {len(sc.lanes)} lane(s) of {len(types)} type(s) "
                f"({', '.join(types)}), {row['public_lanes']} readable "
                f"without a token, from {', '.join(sc.services)}")
            rows.append(row)
        out: Dict[str, Any] = {"scenes": rows, "count": len(rows)}
        if failed:
            out["unreadable"] = failed
        return out

    def describe_scene(self, scene: str) -> Dict[str, Any]:
        sc = self.scene(scene)
        ep = self.catalog.ep_for(sc)
        try:
            index = {e.get("type"): e
                     for e in ep.get_json(f"{WELL_KNOWN}/schemas",
                                          revalidate=False)}
        except SpatialDDSError as e:
            index, schema_error = {}, str(e)
        else:
            schema_error = ""
        lanes = []
        for lane in sc.lanes:
            row: Dict[str, Any] = {"lane": lane.path, "type": lane.type,
                                   "access": lane.access,
                                   "durability": lane.durability}
            if lane.meta.get("accepts_commands"):
                row["accepts_commands"] = True
            if lane.public:
                try:
                    docs, headers = _get(ep, lane.path)
                    row["fqn"] = headers.get("X-SpatialDDS-Type", "")
                    row["records"] = len(docs) if lane.latched else None
                    if not lane.latched:
                        row["records_note"] = ("a stream: the binding holds "
                                               "no records to count")
                except Problem as e:
                    row["records"] = None
                    row["records_note"] = f"{e.status} {e.slug}"
            else:
                row["fqn"] = self._fqn_of(lane) or None
                row["records"] = None
                row["records_note"] = ("not readable without a token, "
                                       "which this server does not hold")
            entry = index.get(row.get("fqn") or "")
            row["schema"] = ({"url": entry.get("url"),
                              "digest": entry.get("digest")}
                             if entry else None)
            w = self.watch_of(sc, lane)
            if w is not None:
                row["watch"] = w.describe()
            elif self._fqn_of(lane) in WORLD_FQNS:
                row["watch"] = {"watching": False,
                                "note": "operator-scoped: no subscription "
                                        "without a token" if not lane.public
                                        else "watching is off"}
            row["rendering"] = self._render_lane(row)
            lanes.append(row)
        out = {"scene": sc.key, "endpoint": sc.base, "services": sc.services,
               "lanes": lanes,
               "types": sorted({l["fqn"] for l in lanes if l.get("fqn")}),
               "records": sum(l["records"] or 0 for l in lanes)}
        if schema_error:
            out["schemas_error"] = schema_error
        return out

    def _render_lane(self, row: Dict[str, Any]) -> str:
        s = f"{row['lane']}: {row.get('fqn') or row['type']}, {row['access']}"
        if row.get("records") is not None:
            s += f", {row['records']} record(s)"
        elif row.get("records_note"):
            s += f", {row['records_note']}"
        if row.get("accepts_commands"):
            s += ", takes commands"
        w = row.get("watch")
        if w and w.get("watching"):
            s += f", watched since {w['since']}"
        return s


def _distance(p: List[float], r: Dict[str, Any]) -> float:
    """From the point to the entity's box if it has one, else to its pose."""
    if r.get("extent"):
        lo, hi = r["extent"]["min_xyz"], r["extent"]["max_xyz"]
        d = [max(lo[i] - p[i], 0.0, p[i] - hi[i]) for i in range(len(p))]
    else:
        t = r["pose"]["t"]
        d = [p[i] - t[i] for i in range(len(p))]
    return math.sqrt(sum(x * x for x in d))


# ---- the protocol ---------------------------------------------------------

INSTRUCTIONS = """\
Read-only access to SpatialDDS world-model scenes, over the SpatialDDS web
binding, as any member of the public sees them. Every record says who
asserted it, on what basis (DECLARED, OBSERVED, DERIVED, AUTHORED), when it
was stamped and how old that stamp is, and what the binding has said about
its writer since this server began watching ("nobody asserts this now" when
the writer is gone). Records the binding withdrew while this server watched
are returned marked as no longer served. This server never merges, resolves
or ranks: competing claims about one proposition are returned side by side,
and deciding what to believe is yours. Start with list_scenes."""

_DOCS = {
    "list_scenes": "The scenes this server reads, each with its endpoint, "
                   "its lane and type counts, and how many lanes a reader "
                   "without a token can read.",
    "describe_scene": "One scene's lanes: each lane's type, access class, "
                      "durability, record count and published schema, from "
                      "the binding's search, resources and schemas index.",
    "get_entities": "The entities a scene serves now, plus any withdrawn "
                    "while this server watched, with pose, extent, asserter, "
                    "basis, stamp and age. type matches a type URI or its "
                    "last segment. near is a point and radius in the "
                    "scene's frame (x, y, optional z, radius_m, optional "
                    "frame), measured to an entity's box where it has one.",
    "get_claims": "Relationship claims, every field verbatim, with a "
                  "one-line rendering. subject matches the subject or the "
                  "object; predicate matches the URI or its last segment; "
                  "asserter matches exactly. Claims about one proposition "
                  "are grouped side by side, never merged or ranked.",
    "get_entity": "One entity, and every claim in which it is the subject "
                  "or the object.",
}


def _result(doc: Dict[str, Any]) -> Any:
    """Compact JSON, plus one human line per record, as two text blocks."""
    from mcp.types import CallToolResult, TextContent
    lines: List[str] = []
    for k in ("scenes", "lanes", "entities", "claims"):
        lines += [r["rendering"] for r in doc.get(k, []) if "rendering" in r]
    if doc.get("entity"):
        lines.insert(0, doc["entity"]["rendering"])
    for k in ("note", "entity_note", "claims_note"):
        if doc.get(k):
            lines.append(doc[k])
    if not lines:
        lines = ["(nothing)"]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)),
                 TextContent(type="text",
                             text=json.dumps(doc, separators=(",", ":"),
                                             sort_keys=True))],
        structured_content=doc)


def build(view: View) -> Any:
    """The MCP server: the five tools, bound to `view`. Needs the extra."""
    require_mcp()
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
    from pydantic import BaseModel, Field

    class Near(BaseModel):
        x: float
        y: float
        z: Optional[float] = None
        radius_m: float = Field(ge=0)
        frame: Optional[str] = None

    # WARNING: a tool call the caller can fix (an unknown scene) is an
    # answer, not news; the SDK logs those at INFO, onto a client's stderr.
    server = MCPServer("spatialdds", version=__version__,
                       instructions=INSTRUCTIONS, log_level="WARNING")
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=True)

    def call(fn, *a, **kw):
        try:
            return _result(fn(*a, **kw))
        except ToolFailure as e:
            raise ToolError(str(e)) from None
        except SpatialDDSError as e:
            raise ToolError(f"the binding refused or could not be reached: "
                            f"{e}") from None

    def list_scenes() -> Any:
        return call(view.list_scenes)

    def describe_scene(scene: str) -> Any:
        return call(view.describe_scene, scene)

    def get_entities(scene: str, type: Optional[str] = None,
                     near: Optional[Near] = None) -> Any:
        return call(view.get_entities, scene, type,
                    near.model_dump() if near is not None else None)

    def get_claims(scene: str, subject: Optional[str] = None,
                   predicate: Optional[str] = None,
                   asserter: Optional[str] = None) -> Any:
        return call(view.get_claims, scene, subject, predicate, asserter)

    def get_entity(scene: str, id: str) -> Any:
        return call(view.get_entity, scene, id)

    import typing
    for fn in (list_scenes, describe_scene, get_entities, get_claims,
               get_entity):
        # This module's annotations are strings (PEP 563), and `Near` is
        # local to this function, so they are resolved here for the SDK.
        fn.__annotations__ = typing.get_type_hints(
            fn, localns={"Near": Near, "Optional": Optional, "Any": Any})
        server.add_tool(fn, name=fn.__name__, description=_DOCS[fn.__name__],
                        annotations=ro, structured_output=False)
    return server


# ---- HTTP: a per-client throttle ------------------------------------------

class Throttle:
    """ASGI middleware: a token bucket per client address.

    The client is the peer as uvicorn reports it, which `serve_http` asks
    to be the first address in `X-Forwarded-For` when the connection comes
    from loopback (a TLS front on the same host), and the socket's peer
    otherwise. A refusal is a 429 with `Retry-After`, before the request
    reaches the protocol.
    """

    def __init__(self, app: Any, rate: float, burst: float) -> None:
        self.app, self.rate, self.burst = app, rate, burst
        self.buckets: Dict[str, Tuple[float, float]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def client(scope: Dict[str, Any]) -> str:
        return (scope.get("client") or ("", 0))[0]

    def take(self, who: str, now: float) -> float:
        """0 if allowed, else the seconds until a token is due."""
        with self.lock:
            tokens, last = self.buckets.get(who, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self.buckets[who] = (tokens - 1, now)
                return 0.0
            self.buckets[who] = (tokens, now)
            if len(self.buckets) > 10000:
                self.buckets.clear()
            return (1 - tokens) / self.rate

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        wait = self.take(self.client(scope), time.monotonic())
        if not wait:
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "rate-limited",
                           "detail": f"more than {_fmt(self.rate)} requests "
                                     f"a second from this client; retry "
                                     f"after the Retry-After header"}).encode()
        await send({"type": "http.response.start", "status": 429,
                    "headers": [(b"content-type", b"application/json"),
                                (b"retry-after",
                                 str(max(1, math.ceil(wait))).encode()),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def serve_http(server: Any, host: str, port: int, rate: float,
               allow_hosts: List[str]) -> None:
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings
    allowed = [f"{host}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for name in allow_hosts:
        allowed.append(name)
        origins.append(f"https://{name}")
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        host=host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=allowed,
            allowed_origins=origins))
    if rate > 0:
        app = Throttle(app, rate, max(1.0, rate * 4))
    print(f"spatialdds-mcp {__version__}: streamable HTTP on "
          f"http://{host}:{port}/mcp"
          + (f", {_fmt(rate)} request(s)/s per client" if rate > 0
             else ", no throttle"), file=sys.stderr, flush=True)
    # Behind a front on the same host every connection is from loopback;
    # uvicorn then takes the client from X-Forwarded-For, from loopback
    # only, which is what the throttle counts. Stated, not left to defaults.
    uvicorn.run(app, host=host, port=port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1,::1")


def _host_port(s: str) -> Tuple[str, int]:
    host, sep, port = s.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"--http takes HOST:PORT, got {s!r}")
    return host.strip("[]"), int(port)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spatialdds-mcp",
        description="An MCP server over the SpatialDDS web binding: live "
                    "world-model scenes, read-only, as any reader without a "
                    "token sees them. Speaks stdio unless --http is given.")
    ap.add_argument("--scene", action="append", default=[], metavar="URL",
                    required=True,
                    help="a binding base URL (every scene its search lists), "
                         "or a base URL and one scene, e.g. "
                         "https://demo.spatialdds.org/ut-austin-south-mall; "
                         "repeatable")
    ap.add_argument("--http", type=_host_port, metavar="HOST:PORT",
                    help="serve streamable HTTP at http://HOST:PORT/mcp "
                         "instead of stdio")
    ap.add_argument("--throttle", type=float, default=5.0, metavar="RATE",
                    help="HTTP only: requests a second per client address, "
                         "bursts of four times that; 0 turns it off "
                         "(default 5)")
    ap.add_argument("--allow-host", action="append", default=[],
                    metavar="NAME",
                    help="HTTP only: a Host this server answers to besides "
                         "loopback, as a TLS front forwards it; repeatable")
    ap.add_argument("--no-watch", action="store_true",
                    help="hold no subscriptions: every record's liveliness "
                         "is then 'unwatched'")
    a = ap.parse_args(argv)
    try:
        require_mcp()
    except MissingExtra as e:
        print(f"spatialdds-mcp: {e}", file=sys.stderr)
        return 2
    try:
        view = View(Catalog(a.scene), watch=not a.no_watch)
    except ValueError as e:
        print(f"spatialdds-mcp: {e}", file=sys.stderr)
        return 2
    view.start()
    server = build(view)
    if a.http:
        serve_http(server, a.http[0], a.http[1], a.throttle, a.allow_host)
    else:
        server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
