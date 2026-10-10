"""The gateway's wire layer: the pinned paths, the headers, the WS envelope.

Everything here implements the surface pinned in the spec repository's
`web-binding/conformance/README.md`. Where a path or a field name looks
arbitrary it is quoted from there, not chosen here, and the places the pinned
surface could not be implemented as written are reported in
`WEB-BINDING-FINDINGS.md` rather than quietly adjusted.

Two rules run through all of it:

**A resource's access class decides its headers, mechanically.** The conformance
definition requires that auth and cacheability never contradict, so an
operator-scoped resource is `no-store` and bearer-gated in the same branch that
names it operator-scoped. There is no path through this file that serves an
observation of a person with a cache header on it.

**Public-cacheable implies CORS-open.** A resource served to anyone without
a credential has no origin worth refusing, so `_cache_headers` sets
`Access-Control-Allow-Origin: *` in the same branch that sets `max-age`.
Operator-scoped resources get neither. The appendix does not say this in so
many words; it is a candidate N.4 clarification, recorded in `docs/EVIDENCE.md`
rather than assumed.

**A bearer token never travels in a URL.** The WebSocket subscription reads
`Authorization` like the GET does. A `?token=` query parameter would be simpler
for a browser and would also write the credential into every proxy log and
`Referer` between here and the client, so it is not offered.
"""
from __future__ import annotations

import json
from typing import Any

from aiohttp import WSMsgType, web

from spatialdds_web import gateway, webjson

WELL_KNOWN = "/.well-known/spatialdds"


def _body(doc: Any) -> bytes:
    """Deterministic JSON. Sorted keys so two starts serve the same bytes.

    MAPPING.md does not require wire JSON to be canonical, only digest input.
    Sorting anyway costs nothing and makes byte-identical output across runs
    something the determinism gate can assert rather than hope for.
    """
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _etag(doc: Any) -> str:
    """A strong ETag over the canonical form, so it is the content digest."""
    return f'"sha256:{webjson.digest(doc)}"'


PROBLEM = "application/problem+json"
PROBLEM_NS = "urn:spatialdds-web:problem:"


def _problem(status: int, slug: str, title: str, detail: str,
             instance: str | None = None, **extra) -> web.Response:
    """An RFC 7807 problem document.

    Appendix N.8 adopts `application/problem+json` and deprecates the ad-hoc
    object this gateway used to return. Aligning with an existing standard
    beats inventing a shape, and carrying two error stories at once is the
    §3.3.0 lesson over again.

    `type` is a service-scoped URN. RFC 7807 problem types belong to the
    service that raises them rather than to the protocol, so the binding
    defines none and naming these here invents nothing at the specification
    level. Any member beyond the five is an RFC 7807 extension member, which
    is where the context the old bodies carried now lives.
    """
    doc: dict[str, Any] = {"type": PROBLEM_NS + slug, "title": title,
                           "status": status, "detail": detail}
    if instance:
        doc["instance"] = instance
    doc.update(extra)
    return web.Response(body=_body(doc), status=status,
                        content_type=PROBLEM,
                        headers={"Cache-Control": "no-store"})


def _cache_headers(access: str, doc_ttl: int | None = None) -> dict[str, str]:
    """Cacheability, reach, and auth decided together or not at all.

    The CORS header lives in this branch rather than in a middleware for the
    same reason the cache header does: a public-cacheable resource is one a
    client may read without presenting anything, so there is no origin that
    should be refused it, and an operator-scoped resource is gated by a
    credential a browser must not be invited to attach ambiently. Putting the
    two in one function makes the pairing impossible to drift apart.

    `Access-Control-Expose-Headers` is here because without it a cross-origin
    reader can see the body but not the ETag or the access class that describes
    it -- the headers would be served and unreadable, which is the instrument
    failing to report what it measures.
    """
    if access == gateway.PUBLIC:
        # N.4: where a public-cacheable resource is a manifest carrying its own
        # `ttl_sec`, that document-level lifetime (§7.5.2) governs its
        # `max-age` in place of the access class's default.
        age = gateway.PUBLIC_MAX_AGE if doc_ttl is None else doc_ttl
        return {"Cache-Control": f"public, max-age={age}",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers":
                    "ETag, X-SpatialDDS-Topic, X-SpatialDDS-Type, "
                    "X-SpatialDDS-Access"}
    # Operator-scoped. Never cacheable and never CORS-open, by the same
    # decision that gated it. `doc_ttl` is ignored here deliberately: N.4
    # grants no lifetime to a gated resource whatever `ttl_sec` it carries,
    # because the cache that would honour it is the shared one the gate exists
    # to keep the body out of.
    return {"Cache-Control": "no-store", "Vary": "Authorization"}


def _authorized(request: web.Request, token: str | None) -> bool:
    if not token:
        return True                       # no token configured: nothing gated
    got = request.headers.get("Authorization", "")
    return got.startswith("Bearer ") and got[7:] == token


def _unauthorized(instance: str | None = None) -> web.Response:
    r = _problem(401, "operator-scoped", "Operator-scoped resource",
                 "This resource is an observation rather than configuration, "
                 "so it requires a bearer credential and is never cacheable.",
                 instance=instance, access=gateway.OPERATOR)
    r.headers["WWW-Authenticate"] = 'Bearer realm="spatialdds"'
    return r


def _json_response(request: web.Request, doc: Any, access: str,
                   extra: dict[str, str] | None = None,
                   doc_ttl: int | None = None) -> web.Response:
    """One JSON resource, with its ETag, its cache class, and 304 handling."""
    etag = _etag(doc)
    headers = {"ETag": etag, **_cache_headers(access, doc_ttl),
               **(extra or {})}
    if request.headers.get("If-None-Match", "") == etag:
        return web.Response(status=304, headers=headers)
    return web.Response(body=_body(doc), status=200,
                        content_type="application/json", headers=headers)


# ---- well-known ------------------------------------------------------------

async def schemas_index(request: web.Request) -> web.Response:
    base = request.app["base"]
    doc = [{"type": fqn,
            "url": f"{base}{WELL_KNOWN}/schemas/{webjson.schema_file_for(fqn)}",
            "digest": f"sha256:{webjson.digest(schema)}"}
           for fqn, schema in sorted(webjson.schemas().items())]
    return _json_response(request, doc, gateway.PUBLIC)


async def schema_one(request: web.Request) -> web.Response:
    name = request.match_info["type"]
    for fqn, schema in webjson.schemas().items():
        if webjson.schema_file_for(fqn) == name:
            return _json_response(request, schema, gateway.PUBLIC)
    raise web.HTTPNotFound(
        body=_body({"type": PROBLEM_NS + "no-such-schema",
                    "title": "No such schema", "status": 404,
                    "detail": f"This endpoint publishes no schema named {name!r}. The index at "
                              f"{WELL_KNOWN}/schemas lists every one it has.",
                    "instance": str(request.rel_url)}),
        content_type=PROBLEM)


async def search(request: web.Request) -> web.Response:
    st: gateway.State = request.app["state"]
    base = request.app["base"]
    geohash = None
    kinds: list[str] = []
    coverage: list[dict] = []
    if request.method == "POST":
        try:
            req = await request.json()
        except Exception:
            raise web.HTTPBadRequest(
                body=_body({"type": PROBLEM_NS + "malformed-body",
                            "title": "Malformed request body", "status": 400,
                            "detail": "The request body is not valid JSON.",
                            "instance": str(request.rel_url)}),
                content_type=PROBLEM)
        if not isinstance(req, dict):
            raise web.HTTPBadRequest(
                body=_body({"type": PROBLEM_NS + "malformed-body",
                            "title": "Malformed request body", "status": 400,
                            "detail": "A search body must be a JSON object, "
                                      "per §3.3.0.",
                            "instance": str(request.rel_url)}),
                content_type=PROBLEM)
        geohash = req.get("geohash")
        kinds = list(req.get("kind") or [])
        coverage = list(req.get("coverage") or [])
    else:
        geohash = request.query.get("geohash")
        k = request.query.get("kind")
        kinds = [k] if k else []
    try:
        doc = gateway.search(st, base, geohash=geohash, kinds=kinds,
                             coverage=coverage)
    except ValueError as e:
        raise web.HTTPBadRequest(
            body=_body({"type": PROBLEM_NS + "bad-query",
                        "title": "Malformed discovery query", "status": 400,
                        "detail": str(e),
                        "instance": str(request.rel_url)}),
            content_type=PROBLEM)
    # Discovery is public; §3.3.0 suggests a short cache for geohash queries.
    return _json_response(request, doc, gateway.PUBLIC)


async def no_dds(request: web.Request) -> web.Response:
    """`bootstrap` on a deployment with no DDS behind it.

    It exists to hand a client the parameters for joining a DDS domain. This
    endpoint is the binding's standalone shape and has no domain, no peers and
    no partitions, so there is nothing truthful to put in a bootstrap manifest.
    404 is what §3.3.0 already defines for a well-known path an authority does
    not support, and a fabricated `domain_id` would be worse than an absence.

    `resolver` used to share this handler and this reason. It no longer does:
    N.2's erratum separates them, because resolver metadata is an authority's
    URI-resolution entry point and was never bootstrap material. Serving the
    DDS reason from that path was finding 20.
    """
    return _problem(
        404, "no-dds-domain", "Not supported by this authority",
        "This endpoint is a standalone web-binding gateway with no DDS domain "
        "behind it, so it has no bootstrap parameters to publish. Appendix "
        "N.2: bootstrap is required only of an endpoint that offers DDS "
        "access, and a 404 from it is conformant.",
        instance=str(request.rel_url),
        served=[f"{WELL_KNOWN}/search", f"{WELL_KNOWN}/schemas"])


async def resolver(request: web.Request) -> web.Response:
    """§7.5.2 resolver metadata, for the URIs this endpoint is authority for.

    N.2, as amended: REQUIRED of an endpoint that is an authority for the
    `spatialdds://` URIs it can resolve; a 404 is conformant from an endpoint
    that is not such an authority, which includes every endpoint holding no
    manifests.
    """
    st: gateway.State = request.app["state"]
    base = request.app["base"]
    doc = gateway.resolver_metadata(st, base)
    if doc is None:
        return _problem(
            404, "not-an-authority", "Not an authority for any SpatialDDS URI",
            f"This endpoint holds no manifest whose URI authority is "
            f"{gateway.base_authority(base) or 'this host'!r}, so it is not an "
            f"authority for any spatialdds:// URI and publishes no resolver "
            f"metadata. Appendix N.2: a 404 here is conformant.",
            instance=str(request.rel_url), held=len(st.manifests))
    return _json_response(request, doc, gateway.PUBLIC)


async def resolve(request: web.Request) -> web.Response:
    """§7.5.2 HTTPS resolution: one URI in, the manifest it names out.

    Errors are the codes §7.5.3 pins: 400 for a URI this is not, 404 for one
    this endpoint does not answer for or does not hold. The two 404 cases are
    deliberately indistinguishable in status and distinguished in the body: a
    client treats both as failure (§7.5.1), and an operator reading a log
    wants to know which.
    """
    st: gateway.State = request.app["state"]
    base = request.app["base"]
    if gateway.resolver_metadata(st, base) is None:
        return _problem(
            404, "not-an-authority", "Not an authority for any SpatialDDS URI",
            "This endpoint publishes no resolver metadata, so it resolves "
            "nothing. Appendix N.2.",
            instance=str(request.rel_url))

    uri = request.query.get("uri", "")
    if not uri:
        return _problem(
            400, "no-uri", "No URI to resolve",
            "§7.5.2 resolves a URI passed as the `uri` query parameter, "
            "percent-encoded. There was none.",
            instance=str(request.rel_url))
    if not gateway.uri_authority(uri):
        return _problem(
            400, "not-a-spatialdds-uri", "Not a resolvable SpatialDDS URI",
            f"A SpatialDDS URI is {gateway.SCHEME}authority/... per Appendix "
            f"F; {uri!r} has no authority to resolve against.",
            instance=str(request.rel_url), uri=uri)

    mine = gateway.base_authority(base)
    theirs = gateway.uri_authority(uri)
    if theirs != mine:
        return _problem(
            404, "foreign-authority", "Another authority's URI",
            f"This endpoint is the authority for {mine!r} and will not answer "
            f"for {theirs!r}. §7.5.2 resolves a URI against its own "
            f"authority's host; answering for a namespace this endpoint does "
            f"not own would be a fabrication, not a convenience.",
            instance=str(request.rel_url), uri=uri, authority=theirs)

    doc = st.manifests.get(uri)
    if doc is None:
        return _problem(
            404, "no-such-manifest", "No manifest for that URI",
            f"This endpoint is the authority for {mine!r} but holds no "
            f"manifest with that id. §7.5.3: 404 is not found.",
            instance=str(request.rel_url), uri=uri,
            held=sorted(st.manifests))

    # N.4, as amended: the document's own `ttl_sec` governs its max-age.
    ttl = doc.get("ttl_sec")
    return _json_response(request, doc, gateway.PUBLIC,
                          extra={"X-SpatialDDS-Access": gateway.PUBLIC},
                          doc_ttl=ttl if isinstance(ttl, int) else None)


# ---- latched resources -----------------------------------------------------

def _resolve(request: web.Request) -> tuple[str, gateway.Resource]:
    st: gateway.State = request.app["state"]
    m = request.match_info
    path = f"/{m['scene']}/{m['stream']}/{m['type']}/{m['version']}"
    res = st.resource_for(path)
    if res is None:
        raise web.HTTPNotFound(
            body=_body({"type": PROBLEM_NS + "no-such-resource",
                        "title": "No such resource", "status": 404,
                        "detail": f"This endpoint serves no topic at {path}.",
                        "instance": str(request.rel_url)}),
            content_type=PROBLEM)
    return res.topic, res


async def collection(request: web.Request) -> web.Response:
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await subscribe(request)
    st: gateway.State = request.app["state"]
    topic, res = _resolve(request)
    if res.access == gateway.OPERATOR and not _authorized(
            request, request.app["token"]):
        return _unauthorized(str(request.rel_url))
    doc = [inst.data for inst in st.latched.get(topic, {}).values()]
    return _json_response(request, doc, res.access,
                          {"X-SpatialDDS-Topic": topic,
                           "X-SpatialDDS-Type": res.fqn,
                           "X-SpatialDDS-Access": res.access})


async def instance(request: web.Request) -> web.Response:
    st: gateway.State = request.app["state"]
    topic, res = _resolve(request)
    if res.access == gateway.OPERATOR and not _authorized(
            request, request.app["token"]):
        return _unauthorized(str(request.rel_url))
    key = request.match_info["key"]
    inst = st.latched.get(topic, {}).get(key)
    if inst is None:
        raise web.HTTPNotFound(
            body=_body({"type": PROBLEM_NS + "no-such-instance",
                        "title": "No such instance", "status": 404,
                        "detail": f"Topic {topic} holds no instance keyed "
                                  f"{key!r}.",
                        "instance": str(request.rel_url),
                        "topic": topic, "key": key}),
            content_type=PROBLEM)
    return _json_response(request, inst.data, res.access,
                          {"X-SpatialDDS-Topic": topic,
                           "X-SpatialDDS-Type": res.fqn,
                           "X-SpatialDDS-Access": res.access})


# ---- the subscription ------------------------------------------------------

async def subscribe(request: web.Request) -> web.StreamResponse:
    """Latched replay, exactly one `end_of_replay`, then live. In that order.

    The marker is the whole point of the lane. On a DDS bus "you now have the
    latched set" is implicit in the QoS and a late joiner simply receives it;
    over a WebSocket a client cannot tell a slow replay from a quiet topic, so
    the binding makes the boundary explicit and the count checkable.
    """
    st: gateway.State = request.app["state"]
    topic, res = _resolve(request)
    if res.access == gateway.OPERATOR and not _authorized(
            request, request.app["token"]):
        return _unauthorized(str(request.rel_url))

    ws = web.WebSocketResponse(heartbeat=None)
    await ws.prepare(request)

    replay = gateway.replay_messages(st, topic)
    for ev in replay:
        await ws.send_str(json.dumps(ev, sort_keys=True))
    await ws.send_str(json.dumps(gateway.end_of_replay(topic, len(replay)),
                                 sort_keys=True))

    for ev in st.live.get(topic, []):
        if ws.closed:
            break
        await ws.send_str(json.dumps(ev, sort_keys=True))

    # The recorded stream is finished. A client must not have to infer that
    # from the socket closing, so the gateway says so per key, which is the
    # one liveliness fact it actually holds. See WEB-BINDING-FINDINGS.md on
    # what this does and does not claim about the upstream producer.
    if not ws.closed:
        for key in list(st.latched.get(topic, {})):
            await ws.send_str(json.dumps(gateway.liveliness(topic, key, False),
                                         sort_keys=True))
        await ws.close()

    # Drain anything the client sent so the close handshake completes cleanly.
    async for msg in ws:
        if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            break
    return ws


# ---- the app ---------------------------------------------------------------

async def root(request: web.Request) -> web.Response:
    """No index endpoint is invented; this is a 404 that says where to look."""
    base = request.app["base"]
    return _problem(
        404, "no-resource-at-root", "No resource at /",
        "This endpoint invents no index endpoint. The well-known paths and the "
        "topic resources it serves are listed below, and the service manifest "
        "at the search path carries each topic's own URL.",
        instance="/",
        well_known=[f"{base}{WELL_KNOWN}/search",
                    f"{base}{WELL_KNOWN}/schemas"],
        resources=sorted(base + p for p in request.app["state"].resources))


def make_app(state: gateway.State, base: str,
             token: str | None) -> web.Application:
    app = web.Application()
    app["state"] = state
    app["base"] = base.rstrip("/")
    app["token"] = token
    app.add_routes([
        web.get("/", root),
        web.get(f"{WELL_KNOWN}/schemas", schemas_index),
        web.get(f"{WELL_KNOWN}/schemas/{{type}}", schema_one),
        web.get(f"{WELL_KNOWN}/search", search),
        web.post(f"{WELL_KNOWN}/search", search),
        web.get(f"{WELL_KNOWN}/bootstrap", no_dds),
        web.get(f"{WELL_KNOWN}/resolver", resolver),
        web.get(gateway.RESOLVE_PATH, resolve),
        # The latched-resource pattern: the on-bus topic name with
        # `spatialdds/` removed, collection then instance.
        web.get("/{scene}/{stream}/{type}/{version}", collection),
        web.get("/{scene}/{stream}/{type}/{version}/{key}", instance),
    ])
    return app
