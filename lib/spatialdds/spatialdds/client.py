"""The client API: one endpoint object and the verbs on it.

Imported lazily by the package's `__init__` so that `import spatialdds.server`
costs nothing from this side. Everything public here is re-exported as
`spatialdds.<name>`; this module is where it is implemented, not where it is
named.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

import requests

from . import canonical, records
from .errors import (IntegrityError, Problem, SpatialDDSError, Unreachable,
                     UnverifiableIntegrity, problem_for)
from .errors import SurfaceOff, Unauthorized

#: What the package's facade resolves through this module. The two exception
#: subclasses are here because this is the module `spatialdds.Unauthorized`
#: comes from, even though nothing in this file raises them by name -- a list
#: states that, where a comment would only assert it.
__all__ = [
    "connect", "Endpoint", "Hit", "Topic", "Resolved", "Written",
    "Sample", "Dispose", "EndOfReplay", "Liveliness",
    "SpatialDDSError", "Unreachable", "Problem", "Unauthorized", "SurfaceOff",
    "IntegrityError", "UnverifiableIntegrity", "lift",
]


WELL_KNOWN = "/.well-known/spatialdds"
_SHA256 = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$", re.I)
lift = canonical.lift


# ---- events ---------------------------------------------------------------

@dataclass
class Sample:
    """One instance, lifted. `data` carries wire values, not canonical JSON."""
    topic: str
    key: str
    data: Dict[str, Any]


@dataclass
class Dispose:
    topic: str
    key: str
    reason: str = ""


@dataclass
class EndOfReplay:
    """The latched set is complete. Before it you are catching up."""
    topic: str
    replayed: int = 0


@dataclass
class Liveliness:
    topic: str
    key: str
    alive: bool = False


def _event(raw: Dict[str, Any], fqn: str, lifted: bool):
    kind = raw.get("event")
    if kind == "sample":
        data = raw.get("data") or {}
        return Sample(raw.get("topic", ""), raw.get("key", ""),
                      canonical.lift(fqn, data) if lifted else data)
    if kind == "dispose":
        return Dispose(raw.get("topic", ""), raw.get("key", ""),
                       raw.get("reason", ""))
    if kind == "end_of_replay":
        return EndOfReplay(raw.get("topic", ""), raw.get("replayed", 0))
    if kind == "liveliness":
        return Liveliness(raw.get("topic", ""), raw.get("key", ""),
                          bool(raw.get("alive")))
    return raw


# ---- the endpoint ---------------------------------------------------------

def connect(base: str, token: Optional[str] = None,
            verify_integrity: bool = True, timeout: float = 15.0,
            lifted: bool = True, scene: Optional[str] = None,
            authority: Optional[str] = None) -> "Endpoint":
    """An endpoint. Nothing is fetched until you ask for something.

    `scene` is the §3.3.1 domain segment the composed publish verbs put
    records under -- the first path segment of every resource URL. One word,
    yours to choose, and on a multi-tenant registry it is your lane. Leave it
    unset and reading works as before, but a publish verb refuses and names
    the scenes the endpoint serves: until 0.6 an unset scene meant `default`,
    which minted a new scene beside the one the caller meant (finding 46).

    `authority` is the DNS name records are addressed under. Left unset, the
    endpoint is asked -- see `Endpoint.authority` -- because the authority is
    the endpoint's to declare and a client that guessed would be inventing a
    namespace.
    """
    return Endpoint(base, token, verify_integrity, timeout, lifted, scene,
                    authority)


class Endpoint:
    """Everything this library does, reached from one object.

    `verify_integrity=True` is the default and means two things: latched reads
    revalidate with the ETag the endpoint gave, and `fetch_asset` checks the
    bytes against the hash the manifest declared. Turning it off is possible
    and deliberately loud -- `verify_integrity=False` at the call site, nowhere
    else -- because a client that silently stops checking is how a corrupted
    asset becomes a mystery three layers away.
    """

    def __init__(self, base: str, token: Optional[str] = None,
                 verify_integrity: bool = True, timeout: float = 15.0,
                 lifted: bool = True, scene: Optional[str] = None,
                 authority: Optional[str] = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.verify_integrity = verify_integrity
        self.timeout = timeout
        self.lifted = lifted
        self.scene = scene
        self._etags: Dict[str, str] = {}
        self._cache: Dict[str, Any] = {}
        self._authority: Optional[str] = authority
        self._s = requests.Session()

    # -- plumbing --
    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return self.base + path_or_url

    def _origin(self, url: str) -> str:
        """scheme://host[:port], lowercased: what a credential is scoped to."""
        scheme, _, rest = url.partition("://")
        return f"{scheme.lower()}://{rest.split('/', 1)[0].lower()}"

    def _headers(self, extra: Optional[Dict[str, str]] = None,
                 url: Optional[str] = None) -> Dict[str, str]:
        """Request headers; the token only for this endpoint's own origin.

        A manifest's asset may live on any host, and `fetch_asset` follows it
        with this same session. Until 0.6 the bearer went with it, handing the
        endpoint's write credential to whoever served the asset -- and failing
        the fetch, since a host that does not know a bearer may refuse it
        (finding 45). The token is scoped to the origin `connect()` was given,
        and nowhere else.
        """
        h = dict(extra or {})
        if self.token and (url is None
                           or self._origin(url) == self._origin(self.base)):
            h["Authorization"] = "Bearer " + self.token
        return h

    def _scene(self, scene: Optional[str]) -> str:
        """The scene a publish verb writes, or a refusal naming the choices."""
        if scene or self.scene:
            return scene or self.scene
        served: set = set()
        try:
            for h in self.search():
                for t in h.topics:
                    path = t.get("url", "").split("//", 1)[-1]
                    served.add(path.split("/")[1] if "/" in path else "")
        except SpatialDDSError:
            pass
        served.discard("")
        raise ValueError(
            "no scene to publish into: pass scene= to connect() or to this "
            "call. A publish never picks one, because a guessed scene is a "
            "new lane nobody asked for (finding 46)."
            + (f" This endpoint serves: {', '.join(sorted(served))}."
               if served else ""))

    def _request(self, method: str, path: str, *, body: Any = None,
                 headers: Optional[Dict[str, str]] = None,
                 params: Optional[Dict[str, Any]] = None,
                 raw: bool = False):
        url = self._url(path)
        h = self._headers(headers, url)
        if body is not None:
            h["Content-Type"] = "application/json"
        try:
            r = self._s.request(method, url, params=params,
                                data=json.dumps(body).encode()
                                if body is not None else None,
                                headers=h, timeout=self.timeout)
        except requests.RequestException as e:
            raise Unreachable(self.base, e) from e
        if r.status_code >= 400:
            try:
                doc = r.json()
            except ValueError:
                doc = {"detail": r.text[:200], "title": r.reason}
            raise problem_for(r.status_code, doc, url, method)
        return r if raw else (r.status_code, r.headers, r)

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None,
                 revalidate: bool = True) -> Any:
        """A GET with the endpoint's ETag honoured on repeat reads."""
        url = self._url(path)
        headers = {}
        if revalidate and self.verify_integrity and url in self._etags:
            headers["If-None-Match"] = self._etags[url]
        r = self._request("GET", path, headers=headers, params=params,
                          raw=True)
        if r.status_code == 304:
            return self._cache[url]
        doc = r.json()
        if "ETag" in r.headers:
            self._etags[url], self._cache[url] = r.headers["ETag"], doc
        return doc

    @property
    def authority(self) -> str:
        """The DNS name this endpoint answers as, from the endpoint itself.

        Three places to ask, in order, and all three are the endpoint
        describing itself rather than this library deciding:

        1. `/.well-known/spatialdds/resolver`, which states the authority
           outright -- but only when the endpoint holds manifests under it,
           since N.2 makes an endpoint an authority for exactly what it can
           resolve. A registry that has only just started holds none.
        2. the 404 document at `/`, which lists this endpoint's own
           well-known URLs. They are built from the base it believes it is
           reached at, so their host is its authority. Reading data out of an
           error document is unusual and deliberate: that document exists to
           say where things are, and it is the only one an empty registry
           serves that carries an absolute URL.
        3. the host of the address you connected to, which is the right answer
           when an endpoint advertises nothing else.

        Pass `authority=` to `connect()` to skip all three.
        """
        if self._authority:
            return self._authority
        try:
            meta = self.get_json(f"{WELL_KNOWN}/resolver", revalidate=False)
            if isinstance(meta, dict) and meta.get("authority"):
                self._authority = str(meta["authority"])
                return self._authority
        except SpatialDDSError:
            pass
        try:
            self._request("GET", "/", raw=True)
        except Problem as e:
            urls = e.doc.get("well_known") or []
            if urls and isinstance(urls[0], str) and "//" in urls[0]:
                self._authority = urls[0].split("//", 1)[1].split("/", 1)[0]
                return self._authority
        except SpatialDDSError:
            pass
        self._authority = self.base.split("//", 1)[-1].split("/", 1)[0]
        return self._authority

    # -- discovery --
    def search(self, geohash: Optional[str] = None,
               kinds: Optional[List[str]] = None) -> List["Hit"]:
        """§3.3.0 search. One cell in, service manifests out."""
        params: Dict[str, Any] = {}
        if geohash:
            params["geohash"] = geohash
        if kinds:
            params["kind"] = ",".join(kinds)
        doc = self.get_json(f"{WELL_KNOWN}/search", params=params or None,
                            revalidate=False)
        return [Hit(self, m) for m in doc.get("results", [])]

    def near(self, lat: float, lon: float, radius_m: float) -> List["Hit"]:
        """Services whose declared coverage reaches a point.

        A helper over `search`, never a second protocol: it picks a geohash
        precision whose cell is at least the radius, encodes the centre and
        eight points around the query circle so the covering cells are found
        without neighbour arithmetic, searches each distinct cell, and then
        filters the hits by their own declared coverage. Everything it sends is
        a §3.3.0 geohash query, and the endpoint answers exactly as it would
        for any other.
        """
        cells = sorted({geohash_encode(y, x, geohash_precision(radius_m))
                        for y, x in _ring(lat, lon, radius_m)})
        seen: Dict[str, "Hit"] = {}
        for cell in cells:
            for hit in self.search(geohash=cell):
                if hit.service_id not in seen and hit.reaches(lat, lon,
                                                              radius_m):
                    seen[hit.service_id] = hit
        return [seen[k] for k in sorted(seen)]

    # -- resolution --
    def resolve(self, uri: str) -> "Resolved":
        """§7.5.2 resolution, through the `https_base` the endpoint advertises."""
        meta = self.get_json(f"{WELL_KNOWN}/resolver", revalidate=False)
        self._authority = meta.get("authority") or self._authority
        hb = meta.get("https_base")
        if not hb:
            raise SpatialDDSError(
                f"{self.base} publishes no resolver metadata, so it resolves "
                f"nothing. It is not an authority for any spatialdds:// URI.")
        path = hb if hb.startswith(self.base) else self.base + _path_of(hb)
        r = self._request("GET", path, params={"uri": uri}, raw=True)
        return Resolved(self, uri, r.json(), r.headers.get("ETag", ""))

    # -- the write surface --
    def publish(self, topic: str, key: str, record: Dict[str, Any],
                if_match: Optional[str] = None) -> "Written":
        """PUT one instance. Returns the served document and its ETag."""
        headers = {"If-Match": if_match} if if_match else None
        path = f"{topic.rstrip('/')}/{_quote_key(key)}"
        r = self._request("PUT", path, body=record, headers=headers, raw=True)
        return Written(r.json(), r.headers.get("ETag", ""),
                       r.status_code == 201)

    def publish_manifest(self, record: Dict[str, Any]) -> "Written":
        """PUT a manifest, keyed by the URI it declares as its own `id`."""
        r = self._request("PUT", "/spatialdds/resolve", body=record, raw=True)
        return Written(r.json(), r.headers.get("ETag", ""),
                       r.status_code == 201)

    # -- the write surface, composed --
    #
    # Three verbs over `records`, and nothing more: each builds one or two
    # records with the builders, PUTs them with `publish`, and hands back what
    # `publish` hands back. The kwargs mirror the builders' so that there is
    # one vocabulary rather than two, and anything the builders cannot express
    # is not reachable from here either -- by design, since the alternative is
    # a convenience layer that quietly becomes the real interface.

    def publish_service(self, service_id: str, kind: str, *, coverage: Any,
                        manifest: Optional[Dict[str, Any]] = None,
                        scene: Optional[str] = None,
                        stream: str = "discovery",
                        topics: Any = (), **kw: Any) -> "Written":
        """Announce a service: one PUT, or two when a manifest comes with it.

        The announce is what search returns; the manifest, if given, is the
        contract behind it and is published first so that the URI the announce
        names resolves the moment the announce is visible.
        """
        sc = self._scene(scene)
        man: Optional["Written"] = None
        if manifest is not None:
            man = self.publish_manifest(manifest)
        rec = records.service_announce(
            service_id, kind, coverage=coverage, authority=self.authority,
            scene=sc, topics=topics,
            manifest_uri=(manifest or {}).get("id") if manifest else None,
            **kw)
        w = self.publish(topic=f"/{sc}/{stream}/announce/v1", key=service_id,
                         record=rec)
        w.manifest = man
        return w

    def publish_content(self, content_id: str, *,
                        position: Optional[Any] = None,
                        coverage: Any = None, radius_m: float = 10.0,
                        asset: Any = None,
                        media_type: str = "application/octet-stream",
                        sha256: str = "", asset_uri: str = "",
                        assets: Any = None,
                        scene: Optional[str] = None,
                        stream: str = "content",
                        title: str = "", summary: str = "",
                        tags: Any = (), class_id: str = "",
                        provider_id: str = "", ttl_sec: int = 3600,
                        when: Any = None) -> "Written":
        """Publish content: its manifest, then the announce that places it.

        Two PUTs, in that order, for the same reason as above. `position` is
        `(lat, lon)` or `(lat, lon, alt_m)` and becomes a coverage circle of
        `radius_m`; pass `coverage=` instead for anything else.

        An `asset=` URL with no `sha256=` is published as an asset with no
        integrity claim, and a reader who fetches it gets
        `UnverifiableIntegrity` rather than a check that silently passed. That
        is the honest default for a URL somebody handed you.
        """
        sc = self._scene(scene)
        cov = coverage
        if cov is None and position is not None:
            lat, lon = float(position[0]), float(position[1])
            alt = float(position[2]) if len(position) > 2 else 0.0
            cov = records.circle(lat, lon, radius_m, alt_m=alt)
        if cov is None:
            raise SpatialDDSError(
                "publish_content needs somewhere to put the content: pass "
                "position=(lat, lon) or coverage=sd.circle(...).")
        items = list(assets) if assets else []
        if asset is not None:
            items.append(self._asset_entry(asset, media_type, sha256,
                                           asset_uri))
        man = records.content_manifest(
            content_id, authority=self.authority, scene=sc, assets=items,
            title=title, summary=summary, tags=tags, class_id=class_id,
            coverage=cov, ttl_sec=ttl_sec, when=when)
        mw = self.publish_manifest(man)
        rec = records.content_announce(
            content_id, coverage=cov, authority=self.authority, scene=sc,
            title=title, summary=summary, tags=tags, class_id=class_id,
            provider_id=provider_id, manifest_uri=man["id"], ttl_sec=ttl_sec,
            when=when)
        w = self.publish(topic=f"/{sc}/{stream}/content_announce/v1",
                         key=content_id, record=rec)
        w.manifest = mw
        return w

    def _asset_entry(self, asset: Any, media_type: str, sha256: str,
                     asset_uri: str) -> Dict[str, Any]:
        """Either asset form, from whatever the caller actually had.

        A dict is used as it stands -- `sd.asset()` and `sd.asset_ref()`
        return those. A URL becomes a reference, with whatever hash was
        supplied. Bytes, or a path to a file that exists, are hashed from the
        data: `sd.asset()` does that, and the URI defaults to this endpoint's
        own asset route under the authority it advertises, which is where the
        bytes will be if you put them there. Pass `asset_uri=` for anywhere
        else.
        """
        if isinstance(asset, dict):
            return asset
        if isinstance(asset, str) and asset.startswith(("http://", "https://")):
            return records.asset_ref(asset, media_type=media_type,
                                     hash=sha256)
        name = ""
        if not isinstance(asset, (bytes, bytearray)):
            name = str(asset).rsplit("/", 1)[-1]
        uri = asset_uri or (f"https://{self.authority}/assets/{name}"
                            if name else "")
        return records.asset(asset, media_type=media_type, uri=uri)

    def publish_anchor(self, anchor_id: str, *, lat: float, lon: float,
                       method: str, alt_m: float = 0.0,
                       content: Optional[str] = None,
                       scene: Optional[str] = None,
                       stream: str = "frames", **kw: Any) -> "Written":
        """Publish an anchor. One PUT.

        `content=` is a content id, and becomes the anchor's `manifest_uri`
        under this endpoint's authority -- the 1.8 revision's content
        reference, used as specified and no further: the binding inherits the
        anchor's own frame, precision and method, and publishing does not
        raise a claim. Pass `manifest_uri=` for a URI you already have.
        """
        sc = self._scene(scene)
        if content is not None and "manifest_uri" not in kw:
            kw["manifest_uri"] = records.content_uri(self.authority, sc,
                                                     content)
        rec = records.geo_anchor(anchor_id, lat=lat, lon=lon, alt_m=alt_m,
                                 method=method, **kw)
        return self.publish(topic=f"/{sc}/{stream}/geo_anchor/v1",
                            key=anchor_id, record=rec)

    def withdraw(self, topic: str, key: str) -> None:
        """DELETE one instance: it leaves the latched set and disposes."""
        self._request("DELETE", f"{topic.rstrip('/')}/{_quote_key(key)}",
                      raw=True)


# ---- what search returns --------------------------------------------------

class Hit:
    """One service manifest, with its topics reachable by name."""

    def __init__(self, ep: Endpoint, manifest: Dict[str, Any]) -> None:
        self.ep, self.manifest = ep, manifest
        self.service = manifest.get("service", {})

    @property
    def service_id(self) -> str:
        return self.service.get("service_id", "")

    @property
    def kind(self) -> str:
        return self.service.get("kind", "")

    @property
    def coverage(self) -> Any:
        return self.manifest.get("coverage")

    @property
    def topics(self) -> List[Dict[str, Any]]:
        return list(self.service.get("topics", []))

    def topic(self, type_name: str) -> "Topic":
        """The topic of that type, with the URL the manifest gave.

        Never constructed from the topic name: the manifest's `url` is the
        hypermedia the binding adds, and following it is the whole point.
        """
        for t in self.topics:
            if t.get("type") == type_name:
                return Topic(self.ep, t)
        have = ", ".join(sorted(t.get("type", "") for t in self.topics))
        raise SpatialDDSError(
            f"{self.service_id or 'this service'} serves no {type_name!r} "
            f"topic; it offers: {have}")

    def serves(self, type_name: str) -> bool:
        """Does this service offer a topic of that type?

        For picking a service out of a search result, which is what `search`
        returns and what nobody should be indexing by position: the order is
        the endpoint's, and it changes the moment somebody publishes another
        service into the same cell. `search(...)[0]` on the front page read
        the fountain's anchors until the page's own publish block ran, and
        then read a VPS that serves none.
        """
        return any(t.get("type") == type_name for t in self.topics)

    def reaches(self, lat: float, lon: float, radius_m: float) -> bool:
        """Does the declared coverage come within `radius_m` of the point?"""
        return _coverage_reaches(self.coverage, lat, lon, radius_m)

    def __repr__(self) -> str:                               # pragma: no cover
        return f"<Hit {self.service_id} kind={self.kind}>"


class Topic:
    """One latched resource, read or streamed."""

    def __init__(self, ep: Endpoint, meta: Dict[str, Any]) -> None:
        self.ep, self.meta = ep, meta

    type = property(lambda self: self.meta.get("type", ""))
    url = property(lambda self: self.meta.get("url", ""))
    name = property(lambda self: self.meta.get("name", ""))
    durability = property(lambda self: self.meta.get("durability", ""))
    access = property(lambda self: self.meta.get("access", ""))

    @property
    def fqn(self) -> str:
        """The IDL type this topic carries, from the schema index."""
        return _FQN_BY_SEG.get(self.type, "")

    def latched(self) -> List[Dict[str, Any]]:
        """Every live instance, lifted."""
        docs = self.ep.get_json(_path_of(self.url))
        if not self.ep.lifted:
            return docs
        return [canonical.lift(self.fqn, d) for d in docs]

    def instance(self, key: str) -> Dict[str, Any]:
        """One instance by its key, percent-encoded as one path segment."""
        doc = self.ep.get_json(f"{_path_of(self.url)}/{_quote_key(key)}")
        return canonical.lift(self.fqn, doc) if self.ep.lifted else doc

    def subscribe(self, timeout: Optional[float] = None) -> Iterator[Any]:
        """Latched replay, exactly one EndOfReplay, then live.

        A synchronous generator, because the shape of this lane is a for loop
        and v0.1 has no async API.
        """
        try:
            from websockets.sync.client import connect as ws_connect
        except ImportError as e:                             # pragma: no cover
            raise SpatialDDSError(
                "subscribe() needs the `websockets` package") from e
        url = _path_of(self.url)
        ws_url = (self.ep.base + url).replace("https://", "wss://", 1) \
            .replace("http://", "ws://", 1)
        headers = self.ep._headers()
        try:
            with ws_connect(ws_url, additional_headers=headers) as ws:
                while True:
                    raw = ws.recv(timeout=timeout)
                    yield _event(json.loads(raw), self.fqn, self.ep.lifted)
        except Exception as e:
            name = type(e).__name__
            if name in ("ConnectionClosedOK", "ConnectionClosed",
                        "ConnectionClosedError"):
                return
            raise


class Resolved:
    """A resolved manifest, and the assets it names."""

    def __init__(self, ep: Endpoint, uri: str, doc: Dict[str, Any],
                 etag: str) -> None:
        self.ep, self.uri, self.doc, self.etag = ep, uri, doc, etag

    @property
    def assets(self) -> List[Dict[str, Any]]:
        return list(self.doc.get("assets", []))

    def asset_url(self, i: int = 0) -> str:
        """The URL this library would fetch for asset `i`.

        An `assets[].uri` is an ordinary URL and usually points somewhere else
        entirely -- a CDN, a release artifact -- and those are fetched exactly
        as written. The one rewrite is for a URL on **this endpoint's own
        authority**: if you are connected to a local copy of an authority that
        advertises another name, the bytes live on the endpoint in front of you
        rather than at the name in the document. Any other host is left alone,
        which an earlier version of this method got wrong by rebasing every
        absolute URL and turning a CDN asset into a 404 here.
        """
        uri = str(self.assets[i].get("uri", ""))
        if not uri.startswith(("http://", "https://")):
            return uri
        if uri.startswith(self.ep.base):
            return uri
        host = uri.split("//", 1)[1].split("/", 1)[0]
        if self.ep._authority and host == self.ep._authority:
            return self.ep.base + _path_of(uri)
        return uri

    def fetch_asset(self, i: int = 0,
                    verify_integrity: Optional[bool] = None) -> bytes:
        """The asset's bytes, checked against the hash the manifest declared.

        Three outcomes, and the third is the point. Bytes that match the
        declared SHA-256 are returned. Bytes that do not raise
        `IntegrityError`. A declared value that is not a SHA-256 of anything
        this library can compute raises `UnverifiableIntegrity` -- because
        SpatialDDS does not say what `checksum`-style integrity fields digest
        (spatialdds-web finding 31), and reporting success for a check that
        did not happen would be worse than refusing.
        """
        want_check = self.ep.verify_integrity if verify_integrity is None \
            else verify_integrity
        a = self.assets[i]
        url = self.asset_url(i)
        r = self.ep._request("GET", url, raw=True)
        raw = r.content
        if not want_check:
            return raw
        declared = str(a.get("hash", ""))
        m = _SHA256.match(declared)
        if not m:
            raise UnverifiableIntegrity(
                f"{url} declares hash {declared!r}, which is not a SHA-256 "
                f"this library can check. SpatialDDS does not specify what "
                f"such a field digests, so the bytes were fetched and not "
                f"verified. Pass verify_integrity=False to accept that.")
        got = hashlib.sha256(raw).hexdigest()
        if got != m.group(1).lower():
            raise IntegrityError(
                f"{url}: {len(raw)} bytes hash to sha256:{got}, and the "
                f"manifest declares {declared}")
        return raw


@dataclass
class Written:
    """What a publish returned: the served form, its ETag, and whether new.

    `manifest` is the second half of a two-PUT verb: `publish_service` and
    `publish_content` publish a manifest and then the record that points at
    it, and this is what the manifest's own PUT returned. None for a plain
    `publish`, and for a service announced without one.
    """
    doc: Dict[str, Any]
    etag: str = ""
    created: bool = False
    manifest: Optional["Written"] = None


# ---- geohash and coverage, for near() -------------------------------------

_G32 = "0123456789bcdefghjkmnpqrstuvwxyz"
#: Rough north-south extent of a geohash cell, metres, by precision.
_CELL_M = {1: 5000000.0, 2: 1250000.0, 3: 156000.0, 4: 39000.0, 5: 4900.0,
           6: 1200.0, 7: 152.0, 8: 38.0, 9: 4.8}


def geohash_encode(lat: float, lon: float, precision: int = 7) -> str:
    """The published geohash encoding. Twenty lines, so not a dependency."""
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon > mid:
                ch |= 1 << (4 - bit); lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat > mid:
                ch |= 1 << (4 - bit); lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_G32[ch]); bit, ch = 0, 0
    return "".join(out)


def geohash_precision(radius_m: float) -> int:
    """The coarsest precision whose cell still covers the radius."""
    for p in range(9, 0, -1):
        if _CELL_M[p] >= radius_m * 2:
            return p
    return 1


def _ring(lat: float, lon: float, radius_m: float):
    """The centre and eight points around the query circle."""
    dlat = radius_m / 111320.0
    dlon = radius_m / max(1.0, 111320.0 * math.cos(math.radians(lat)))
    return [(lat, lon)] + [(lat + a * dlat, lon + b * dlon)
                           for a in (-1, 0, 1) for b in (-1, 0, 1)
                           if (a, b) != (0, 0)]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _coverage_reaches(cov: Any, lat: float, lon: float, radius_m: float) -> bool:
    for el in _elements(cov):
        if el.get("global"):
            return True
        if el.get("has_circle") and el.get("circle_center"):
            c = el["circle_center"]
            if _haversine_m(lat, lon, float(c[1]), float(c[0])) <= \
                    radius_m + float(el.get("circle_radius_m") or 0.0):
                return True
        if el.get("has_bbox") and el.get("bbox"):
            w, s, e, n = (float(x) for x in el["bbox"][:4])
            dlat = radius_m / 111320.0
            dlon = radius_m / max(1.0, 111320.0 * math.cos(math.radians(lat)))
            if not (lon + dlon < w or lon - dlon > e
                    or lat + dlat < s or lat - dlat > n):
                return True
    return False


def _elements(cov: Any) -> List[Dict[str, Any]]:
    if isinstance(cov, list):
        return [c for c in cov if isinstance(c, dict)]
    if isinstance(cov, dict):
        inner = cov.get("elements")
        return ([c for c in inner if isinstance(c, dict)] if isinstance(inner, list)
                else [cov])
    return []


# ---- small helpers --------------------------------------------------------

def _path_of(url: str) -> str:
    if url.startswith(("http://", "https://")):
        rest = url.split("//", 1)[1]
        return "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return url


def _quote_key(key: str) -> str:
    """An instance key is one path segment, so `/` is percent-encoded (N.3)."""
    from urllib.parse import quote
    return quote(key, safe="")


#: Topic type segment -> IDL type, for lifting what a topic carries. Derived
#: from the vendored tables' own names rather than hardcoded: the segment is
#: the type's last name in snake_case, which is how §3.3.1 builds it.
def _fqn_by_segment() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for fqn, entry in canonical.tables().items():
        if entry.get("kind") != "struct":
            continue
        last = fqn.rsplit(".", 1)[-1]
        seg = re.sub(r"(?<!^)(?=[A-Z])", "_", last).lower()
        seg = seg.replace("detection2_d", "detection2d") \
                 .replace("detection3_d", "detection3d")
        out.setdefault(seg, fqn)
    return out


_FQN_BY_SEG = _fqn_by_segment()
