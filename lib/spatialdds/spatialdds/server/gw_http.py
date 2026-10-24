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

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from . import commands, gateway, limits as limits_, publish, tenancy, webjson

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


# The media types the committed assets actually are. Guessing from an
# extension at large is how a server ends up serving JavaScript as text.
MEDIA = {".gltf": "model/gltf+json", ".txt": "text/plain",
         ".json": "application/json", ".glb": "model/gltf-binary"}
ASSET_PATH = "/assets"

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


#: What a cross-origin reader may see beyond the body. Without it the ETag and
#: the access class are served and unreadable, which is the instrument failing
#: to report what it measures.
EXPOSE_HEADERS = ("ETag, X-SpatialDDS-Topic, X-SpatialDDS-Type, "
                  "X-SpatialDDS-Access")

#: Request headers a browser may send to this gateway. `Authorization` is the
#: one that matters and the one that is not CORS-safelisted, so a request
#: carrying it preflights; `Content-Type` is needed for any JSON body, and
#: `If-None-Match` for conditional GETs.
ALLOW_HEADERS = "Authorization, Content-Type, If-None-Match"

#: Methods this gateway serves. Stated once so the preflight answer and the
#: `Allow` header on a refusal cannot drift apart.
ALLOW_METHODS = "GET, HEAD, POST, PUT, DELETE, OPTIONS"

#: How long a browser may cache a preflight. Ten minutes: long enough that a
#: page does not preflight every command, short enough that changing the
#: policy takes effect inside a session.
PREFLIGHT_MAX_AGE = 600


def _cache_headers(access: str, doc_ttl: int | None = None,
                   origin: str | None = None) -> dict[str, str]:
    """Cacheability, reach, and auth decided together or not at all.

    The CORS header lives in this branch rather than in a middleware for the
    same reason the cache header does: a public-cacheable resource is one a
    client may read without presenting anything, so there is no origin that
    should be refused it. Putting the two in one function makes the pairing
    impossible to drift apart.

    **An operator-scoped response now carries an echoed origin too, and the
    reasoning that said otherwise was close to right.** It read: gated by "a
    credential a browser must not be invited to attach ambiently". The
    invitation is real and `Access-Control-Allow-Origin` is not how it is
    issued --- `Access-Control-Allow-Credentials` is. Cookies and HTTP auth
    ride along only when that header says so and the client asks for
    `credentials: 'include'`; this gateway sets it nowhere and never will, so
    nothing here is ambient. A `Bearer` token in an `Authorization` header is
    the opposite of ambient: the page had to hold it and choose to attach it.

    Refusing the origin did not keep that credential out. It made an
    authorized response unreadable by the client that authorized itself ---
    measured from `SpatialDDS-demo`'s browser lane, where a page holding a
    valid token could not read a word of a 200 it had earned. So the origin is
    echoed rather than `*`, with `Vary: Origin`, which keeps a shared cache
    from serving one origin's answer to another; the body stays `no-store`
    regardless, and the credential is still required. No gate is weakened.
    """
    if access == gateway.PUBLIC:
        # N.4: where a public-cacheable resource is a manifest carrying its own
        # `ttl_sec`, that document-level lifetime (§7.5.2) governs its
        # `max-age` in place of the access class's default.
        age = gateway.PUBLIC_MAX_AGE if doc_ttl is None else doc_ttl
        return {"Cache-Control": f"public, max-age={age}",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": EXPOSE_HEADERS}
    # Operator-scoped. Never cacheable, by the same decision that gated it.
    # `doc_ttl` is ignored here deliberately: N.4 grants no lifetime to a gated
    # resource whatever `ttl_sec` it carries, because the cache that would
    # honour it is the shared one the gate exists to keep the body out of.
    headers = {"Cache-Control": "no-store", "Vary": "Authorization"}
    if origin:
        # Echoed, not `*`: a wildcard on a response that varies by credential
        # is the combination a shared cache can get wrong, and there is no
        # reason to name an origin this request did not come from.
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin, Authorization"
        headers["Access-Control-Expose-Headers"] = EXPOSE_HEADERS
    return headers


def _limit_problem(slug: str, detail: str, request: web.Request,
                   **extra) -> web.Response:
    status, title, _when = limits_.REFUSALS[slug]
    return _problem(status, slug, title, detail,
                    instance=str(request.rel_url), **extra)


@web.middleware
async def _limited(request: web.Request, handler) -> web.StreamResponse:
    """The per-client rate and the body limit, as problem documents.

    The body limit is checked on the declared length before anything is read,
    and again by aiohttp's own `client_max_size` for a body that declares
    none; both answers are the same N.8 document naming the limit.
    """
    lim: limits_.Limits = request.app["limits"]
    peer = request.remote
    who = lim.client(peer, request.headers.get("X-Forwarded-For"))
    wait = lim.take(who) if who is not None else 0.0
    if wait > 0:
        r = _limit_problem(
            "rate-limited",
            f"More than {lim.rate_per_s:g} requests per second (burst "
            f"{lim.burst}) from one client. Retry after {wait:.1f} s.",
            request, limit_per_s=lim.rate_per_s, burst=lim.burst)
        r.headers["Retry-After"] = str(max(1, int(wait + 0.999)))
        return r
    if len(request.raw_path) > lim.max_url:
        return _limit_problem(
            "uri-too-long",
            f"This endpoint accepts a path and query up to {lim.max_url} "
            f"bytes; this one is {len(request.raw_path)}.", request,
            limit_bytes=lim.max_url)
    length = request.content_length
    if length is not None and length > lim.max_body:
        return _limit_problem(
            "body-too-large",
            f"This endpoint accepts request bodies up to {lim.max_body} "
            f"bytes; this one declares {length}.", request,
            limit_bytes=lim.max_body)
    # A write's body is N.1 canonical JSON, and says so. A JSON-looking body
    # under another type is refused rather than guessed at: a client that
    # mislabels its body has a bug the endpoint should name, not absorb.
    if request.method in ("PUT", "POST") and request.can_read_body:
        ctype = request.content_type
        if not (ctype == "application/json" or ctype.endswith("+json")):
            return _limit_problem(
                "unsupported-media-type",
                f"A request body here is JSON, sent as application/json; "
                f"this one is {ctype or 'untyped'}.", request,
                content_type=ctype)
    try:
        return await handler(request)
    except web.HTTPRequestEntityTooLarge:
        return _limit_problem(
            "body-too-large",
            f"This endpoint accepts request bodies up to {lim.max_body} "
            f"bytes.", request, limit_bytes=lim.max_body)
    except web.HTTPException as e:
        # The router's own 404 and 405 are plain text. Every refusal this
        # endpoint makes is an N.8 document, the router's included; a handler
        # that already raised one is passed through untouched.
        if e.content_type == PROBLEM or e.status < 400:
            raise
        return _problem(e.status, "no-route" if e.status in (404, 405)
                        else "http-error", e.reason,
                        f"{request.method} {request.path} is not a route this "
                        f"endpoint serves." if e.status in (404, 405)
                        else e.text or e.reason,
                        instance=str(request.rel_url))


def _bad_key(key: str) -> bool:
    """A key with a control character (NUL, CR, LF, ...) or none at all.

    A key is a record's own `@key` member and one URL segment; it is never a
    filesystem path, so `..` is just two dots, but a control character in it
    is never a name anyone meant and would reach the journal and every log.
    """
    return not key or any(ord(c) < 0x20 or ord(c) == 0x7f for c in key)


def _bad_key_problem(request: web.Request, key: str) -> web.Response:
    return _problem(400, "bad-key", "Unusable instance key",
                    f"An instance key is a non-empty name without control "
                    f"characters; this one is {key!r}.",
                    instance=str(request.rel_url))


def _unwritable(request: web.Request,
                e: "publish.JournalUnwritable") -> web.Response:
    """503: the journal could not be written, so nothing was accepted.

    Reads keep serving the state as it was. The write may be retried as it
    stands once space returns; no operator action is needed, because the
    journal on disk is the last one that was completely written.
    """
    r = _problem(503, "journal-unwritable", "The registry cannot record writes",
                 f"This write was not accepted: the journal could not be "
                 f"written ({e}). Reads are unaffected. Retry later.",
                 instance=str(request.rel_url))
    r.headers["Retry-After"] = "60"
    return r


def _scene_records(st: gateway.State, scene: str) -> int:
    """Instances plus manifests a scene holds: what `max_records` counts."""
    n = sum(len(st.latched.get(r.topic, {})) for r in st.resources.values()
            if r.scene == scene)
    return n + sum(1 for u in st.manifests if gateway.uri_scene(u) == scene)


def _over_quota(request: web.Request, who: "tenancy.Principal",
                scene: str) -> web.Response | None:
    if who.max_records is None:
        return None
    held = _scene_records(request.app["state"], scene)
    if held < who.max_records:
        return None
    status, title, _when = tenancy.WRITE_REFUSALS["quota-exceeded"]
    return _problem(status, "quota-exceeded", title,
                    f"Scene {scene!r} holds {held} record(s) and the quota "
                    f"for {who.name!r} is {who.max_records}. Replacing an "
                    f"existing record is still allowed; withdraw one to make "
                    f"room for a new one.", instance=str(request.rel_url),
                    scene=scene, limit_records=who.max_records)


@web.middleware
async def _reach(request: web.Request, handler) -> web.StreamResponse:
    """Make sure every answer names the origin that asked for it.

    **A backstop, not the policy.** `_cache_headers` remains the single place
    that decides cacheability and reach together, and where it has already set
    `Access-Control-Allow-Origin` this changes nothing. What it covers is the
    responses that set no reach at all, because they are built by hand rather
    than through that function --- and the reason it exists is that threading
    the origin explicitly through every construction site was tried first, in
    0.5.3, and missed two:

    * `post_command`'s `202`. The command published and the page could not
      read the outcome: a cross-origin `fetch` rejects a response with no
      allowed origin, so `bindingCommand` in the demo's client landed in its
      `catch` with no status at all, for a command that had in fact succeeded.
    * **Every N.8 problem document.** `_problem` set `no-store` and nothing
      else, so a `401`, `409` or `400` was unreadable by the client it was
      written for. N.8 exists to hand a client a *reason*; a reason a browser
      cannot read is the surface failing at the one thing that distinguishes
      it from a bare status.

    Both were found by curling the running demo after 0.5.3 shipped, not by
    this repository's gates --- the same way the original defect was found,
    which is the lesson repeating rather than a new one.

    Only the origin is echoed here, and only when absent. `Vary: Origin` goes
    with it so a shared cache cannot serve one origin's answer to another; the
    `Expose-Headers` set is added for the same reason it is on a GET, since a
    reader that cannot see the ETag cannot revalidate. `Allow-Credentials` is
    still set nowhere.
    """
    response = await handler(request)
    origin = request.headers.get("Origin")
    if origin and "Access-Control-Allow-Origin" not in response.headers:
        response.headers["Access-Control-Allow-Origin"] = origin
        vary = response.headers.get("Vary")
        response.headers["Vary"] = f"{vary}, Origin" if vary else "Origin"
        response.headers.setdefault("Access-Control-Expose-Headers",
                                    EXPOSE_HEADERS)
    return response


async def preflight(request: web.Request) -> web.Response:
    """Answer the `OPTIONS` a browser sends before a credentialed request.

    **Why this route has to exist, and why nothing in the specification said
    so.** `Authorization` is not a CORS-safelisted request header, so a
    cross-origin request carrying one is preceded by a preflight. This gateway
    routed no `OPTIONS` at all, so aiohttp answered `405` with
    `Allow: GET,HEAD,POST` --- and the browser stopped there, never making the
    request. Two consequences, both measured against 0.5.2 from a page on
    another origin:

    * N.6 was unreachable from a browser for **every** access class, because a
      command always carries a credential. Reclassifying a lane cannot help:
      the preflight is triggered by the request, not by what the resource is.
    * A `public-cacheable` GET that answers `200` with
      `Access-Control-Allow-Origin: *` failed outright if the client
      volunteered a credential it did not need.

    Both are fixed here rather than in Appendix N, because nothing in N
    forbade any of it --- a conforming endpoint could simply omit it, which is
    the separate gap filed for the specification: a binding whose flagship
    client is a browser should have to *state* its preflight behaviour.

    `Allow-Credentials` is deliberately absent. It is the header that would
    invite cookies and HTTP auth to ride along ambiently, and this gateway
    wants the opposite: a credential the page holds and chooses to attach.

    A bare `OPTIONS` with no `Origin` is not a preflight; it gets the same
    `Allow` list as a courtesy, which also keeps `ALLOW_METHODS` the single
    place that answer is written.
    """
    origin = request.headers.get("Origin")
    headers = {"Allow": ALLOW_METHODS}
    if origin:
        headers.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": ALLOW_METHODS,
            # Echo what was asked for when it was asked, so a header this
            # gateway has not thought of is refused rather than blanket-allowed,
            # and fall back to the list above for a preflight that names none.
            "Access-Control-Allow-Headers":
                request.headers.get("Access-Control-Request-Headers")
                or ALLOW_HEADERS,
            "Access-Control-Max-Age": str(PREFLIGHT_MAX_AGE),
            "Access-Control-Expose-Headers": EXPOSE_HEADERS,
            # The answer depends on all three, so a cache must not reuse it
            # across different ones.
            "Vary": "Origin, Access-Control-Request-Method, "
                    "Access-Control-Request-Headers",
        })
    return web.Response(status=204, headers=headers)


def _principal(request: web.Request) -> "tenancy.Principal | None":
    """The principal the request's bearer token names, or None."""
    return request.app["tokens"].authenticate(
        request.headers.get("Authorization", ""))


def _authorized(request: web.Request) -> bool:
    """Any principal at all: what an operator-scoped *read* needs (N.4).

    Access class is observation versus configuration, not a lane, so a read of
    an operator-scoped resource wants a credential and any principal's will
    do. Writes go through `_writer`, which also checks the lane.
    """
    if not request.app["tokens"]:
        return True                       # no token configured: nothing gated
    return _principal(request) is not None


def _writer(request: web.Request,
            scene: str) -> "tenancy.Principal | web.Response":
    """The principal allowed to write `scene`, or the refusal to send.

    401 for no credential or one this endpoint does not know; 403 as a problem
    document for a known principal outside its lane, or a scene that does not
    exist and that this principal may not create. With no table configured at
    all, writes are ungated exactly as before, under an anonymous principal.
    """
    table: tenancy.TokenTable = request.app["tokens"]
    if not table:
        return tenancy.Principal("", "", frozenset({tenancy.ALL}), True)
    p = _principal(request)
    if p is None:
        return _unauthorized(str(request.rel_url))
    refused = tenancy.check_write(p, scene, request.app["state"].scenes, table)
    if refused is not None:
        status, slug, title, detail = refused
        return _problem(status, slug, title, detail,
                        instance=str(request.rel_url), principal=p.name,
                        scene=scene, granted=sorted(p.scenes))
    return p


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
    headers = {"ETag": etag,
               **_cache_headers(access, doc_ttl,
                                request.headers.get("Origin")),
               **(extra or {})}
    if request.headers.get("If-None-Match", "") == etag:
        return web.Response(status=304, headers=headers)
    return web.Response(body=_body(doc), status=200,
                        content_type="application/json", headers=headers)


# ---- well-known ------------------------------------------------------------

#: The generated OpenAPI document and its reference page (tools/gen_openapi.py).
OPENAPI = Path(__file__).resolve().parent.parent / "artifacts" / "openapi.yaml"
OPENAPI_HTML = OPENAPI.with_suffix(".html")
#: The one line of the committed document that names a server; served with
#: this endpoint's own base in its place.
OPENAPI_SERVER = "- url: https://demo.spatialdds.org\n"


async def openapi(request: web.Request) -> web.Response:
    """The REST surface as OpenAPI 3.1, generated, with this endpoint's base.

    Served from the committed artifact rather than built per request: the
    document is a build product with a drift gate, and the gateway has no
    YAML dependency. Only the server URL is this endpoint's own.
    """
    text = OPENAPI.read_text()
    if OPENAPI_SERVER not in text:
        return _problem(500, "openapi-unservable", "OpenAPI document unusable",
                        "The packaged document carries no server line to "
                        "point at this endpoint; regenerate it.")
    text = text.replace(OPENAPI_SERVER, f"- url: {request.app['base']}\n", 1)
    return _text_response(request, text, "application/yaml")


async def openapi_page(request: web.Request) -> web.Response:
    """The static reference page rendered from the same document."""
    return _text_response(request, OPENAPI_HTML.read_text(), "text/html")


def _text_response(request: web.Request, text: str,
                   content_type: str) -> web.Response:
    etag = '"sha256:' + hashlib.sha256(text.encode()).hexdigest() + '"'
    headers = {"ETag": etag,
               **_cache_headers(gateway.PUBLIC, None,
                                request.headers.get("Origin"))}
    if request.headers.get("If-None-Match", "") == etag:
        return web.Response(status=304, headers=headers)
    return web.Response(text=text, content_type=content_type,
                        charset="utf-8", headers=headers)


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
        except web.HTTPRequestEntityTooLarge:
            raise                 # _limited answers it, as the body limit
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
                             coverage=coverage,
                             accepts_commands=request.app["commands"].accepts)
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
    if res.access == gateway.OPERATOR and not _authorized(request):
        return _unauthorized(str(request.rel_url))
    doc = [inst.data for inst in st.latched.get(topic, {}).values()]
    return _json_response(request, doc, res.access,
                          {"X-SpatialDDS-Topic": topic,
                           "X-SpatialDDS-Type": res.fqn,
                           "X-SpatialDDS-Access": res.access})


async def instance(request: web.Request) -> web.Response:
    st: gateway.State = request.app["state"]
    topic, res = _resolve(request)
    if res.access == gateway.OPERATOR and not _authorized(request):
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


def asset_path(root: Path, name: str) -> Path | None:
    """The file a requested asset name means, or None if it is not one.

    The rule is load-bearing, not defensive: aiohttp matches `{name}` against
    the raw path segment and URL-decodes it afterwards, so `..%2F..%2Fx`
    arrives here as a name containing slashes and `(root / name).resolve()`
    would happily leave the directory. Pulled out of the handler so it can
    also be checked directly, over names a request could not carry.
    """
    # A NUL (or any control character) is no file name, and reaching
    # `resolve()` with one raised ValueError inside the handler: a 500 with a
    # traceback in the log, found by gates/security.py.
    if not name or any(ord(c) < 0x20 or ord(c) == 0x7f for c in name):
        return None
    target = (root / name).resolve()
    inside = str(target).startswith(str(root.resolve()) + "/")
    return target if inside and target.is_file() else None


async def asset(request: web.Request) -> web.Response:
    """The bytes a content manifest's ref points at.

    **Implementation-defined, like the write surface.** Appendix N defines no
    asset route: a manifest's `assets[].uri` is a plain HTTPS URL and nothing
    says the endpoint that serves the manifest also serves the bytes. Most
    deployments will point those URLs at a CDN, and this repository's committed
    fountain manifests point at raw.githubusercontent.

    It exists because the publish walkthrough has to be followable without a
    push and without the network: a chain that ends at a URL only reachable
    after this commit is published is not a chain anyone can run. So the duck's
    ref points here, the walkthrough fetches real bytes, and the gate
    byte-compares them against the committed file. Swap the authored ref back
    to an external URL and this route is unused -- it is a convenience for the
    demo, not a claim about the binding.

    Public-cacheable, ETag over the bytes, no directory traversal: one path
    segment, resolved inside the assets directory and refused otherwise.
    """
    root: Path | None = request.app["assets"]
    name = request.match_info["name"]
    if root is None:
        return _problem(404, "no-assets", "This endpoint serves no assets",
                        "No asset directory was configured.",
                        instance=str(request.rel_url))
    target = asset_path(root, name)
    if target is None:
        return _problem(
            404, "no-such-asset", "No such asset",
            f"This endpoint serves no asset named {name!r}.",
            instance=str(request.rel_url), held=sorted(
                f.name for f in root.iterdir() if f.is_file()))
    raw = target.read_bytes()
    etag = f'"sha256:{hashlib.sha256(raw).hexdigest()}"'
    headers = {"ETag": etag, **_cache_headers(gateway.PUBLIC)}
    if request.headers.get("If-None-Match", "") == etag:
        return web.Response(status=304, headers=headers)
    return web.Response(body=raw, status=200, headers=headers,
                        content_type=MEDIA.get(target.suffix,
                                               "application/octet-stream"))


# ---- the write surface -----------------------------------------------------
# Implementation-defined, off unless `--publish` is given, and operator-scoped
# when it is on. A read-only client cannot tell a published instance from a
# replayed one, which is the property that keeps the endpoint conformant with
# this enabled. See `spatialdds.server/publish.py` and docs/PUBLISH.md.

def _disabled(request: web.Request) -> web.Response:
    return _problem(
        405, "publish-disabled", "This endpoint is read-only",
        "The publish surface is an implementation-defined extension and is "
        "off unless the endpoint was started with --publish. Nothing about "
        "the read side changes either way.",
        instance=str(request.rel_url))


def _rejected(r: publish.Rejected, request: web.Request) -> web.Response:
    return _problem(r.status, r.slug, r.title, r.detail,
                    instance=str(request.rel_url), **r.extra)


def _written(request: web.Request, doc: Any, created: bool,
             extra: dict[str, str]) -> web.Response:
    """201 on create, 200 on replace, and the served canonical form back.

    The body is what every reader will now see, and it carries the instance's
    ETag, so a publisher holds the served representation without a second
    request. The response is operator-scoped, hence `no-store`.
    """
    return _json_response(request, doc, gateway.OPERATOR, extra) \
        if not created else web.Response(
            body=_body(doc), status=201, content_type="application/json",
            headers={"ETag": _etag(doc),
                     **_cache_headers(gateway.OPERATOR, None,
                                      request.headers.get("Origin")),
                     **extra})


def _running_loop():
    """The loop running in *this* thread, or None. Never raises."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


async def _remember_loop(app: web.Application) -> None:
    """Record the serving loop, so `notify` can reach it from a thread.

    An `on_startup` signal rather than `make_app`, because `make_app` is
    ordinarily called before there is a loop to record -- an embedder builds
    the app, then hands it to `run_app`.
    """
    app["loop"] = asyncio.get_running_loop()


def notify(app: web.Application, topic: str, event: dict) -> None:
    """Hand an event to every subscriber currently on that topic.

    **The second half of `publish.apply_sample`, and the reason this is
    public.** `apply_sample` says what a *new* subscriber will replay;
    this wakes the ones already connected. Until 0.5.0 only the first half
    was public, so an embedder with a live backend could update the latched
    set --- new subscribers and the collection GET saw it --- and had no way
    to reach an open socket except to reimplement the two lines below against
    `app["subs"]`, which is the one `make_app` key that is not a parameter.

    Measured on 2026-10-17 while embedding this gateway against a live DDS
    bus: `apply_sample` alone, with a subscriber connected and drained quiet,
    delivered nothing for three seconds; the same event written into
    `app["subs"]` by hand arrived immediately. One missing function, and the
    workaround was vendoring a private one.

    So the pair is the contract for a live backend:

        publish.apply_sample(st, topic, res, key, data)   # what replays
        gw_http.notify(app, topic, {"event": "sample",    # who hears it now
                                    "topic": topic,
                                    "key": key, "data": data})

    Synchronous on purpose: there is nothing to await, so a backend reading a
    DDS bus from a thread can call it without arranging a loop. **That use is
    now actually safe**, which it was not in 0.5.0 and 0.5.1: this put onto
    the queue directly, and `asyncio.Queue.put_nowait` from a foreign thread
    enqueues without waking the selector, so a parked subscriber resumed only
    when the loop happened to wake for some other reason. On a busy endpoint
    that is immediate and the bug invisible; on an idle one the events sat
    unseen. The docstring promised the thread case before the code delivered
    it, which is the worse half of the defect.

    Subscriptions only stay open to be woken when `live_subscriptions` is on
    --- see `make_app`. Calling this against an endpoint serving a finished
    recording is harmless and reaches nobody.
    """
    loop = app.get("loop")
    for q in list(app["subs"].get(topic, ())):
        if loop is None or loop is _running_loop():
            q.put_nowait(event)
        else:
            # `asyncio.Queue.put_nowait` is not thread-safe, and the part
            # that bites is not the enqueue -- it is the wakeup. It resolves
            # the parked getter's future directly, and resolving a future
            # from a foreign thread schedules the callback without waking
            # the selector, so the subscriber's `await q.get()` resumes only
            # when the loop next happens to wake for some other reason. On a
            # busy endpoint that is immediate and the bug is invisible; on an
            # idle one the events sit in the queue.
            #
            # Measured on 2026-10-17: a bridge pumping 202 samples into two
            # registered subscriber queues delivered 0 live events, while the
            # same call from the loop thread delivered every one.
            loop.call_soon_threadsafe(q.put_nowait, event)


async def _broadcast(request: web.Request, topic: str, event: dict) -> None:
    """`notify` for a request handler. Kept so its two call sites read as
    they did; the seam is `notify`, which takes the app rather than a
    request because a live backend has one and not the other."""
    notify(request.app, topic, event)


async def post_command(request: web.Request) -> web.Response:
    """N.6: a command is a POST to the resource it acts on.

    The resource is the lane, not the instance, and the addressee rides in the
    body. That is M.1's shape -- "a shared keyed command topic, keyed by the
    addressee" -- and it is also the URL the manifest advertises, which is the
    URL a client holding a manifest already has. Posting to an instance path
    would make a client build a URL by string surgery on a key, which is the
    thing N.7's `url` member exists to abolish.

    Four outcomes, and the status carries which:

    * **202** the backend accepted it. Acceptance, not completion: N.6
      requires no completion tracking, so this gateway invents none.
    * **409** the backend declined, with M.1's reason in the problem document.
      Not a 200: a caller that checks only the status must not read a refusal
      as an acceptance, which would turn M.1's first-class outcome back into
      the absence it exists to replace.
    * **405** no handler for this type, or this lane is not a command lane.
      N.6 names 405 or 404 for an endpoint with nothing to accept a verb for,
      and the body is still an N.8 problem document so the absence is stated
      rather than left to a framework's default page.
    * **400/401** the body is not valid for the type, or no bearer credential.
      Commands are writes, so N.4's operator-scoped path applies.
    """
    st: gateway.State = request.app["state"]
    reg: commands.Registry = request.app["commands"]
    m = request.match_info
    path = f"/{m['scene']}/{m['stream']}/{m['type']}/{m['version']}"
    res = st.resources.get(path)

    # Refuse before authenticating when there is nothing here to command: a
    # 401 would tell an anonymous caller that a lane exists, and the lane's
    # existence is public information that this endpoint simply does not have.
    if res is None:
        return _rejected(publish.Rejected(
            405, "no-command-lane", "No command lane here",
            f"This endpoint serves no resource at {path}, so there is no verb "
            f"to accept. N.6: an endpoint with no command lane refuses "
            f"cleanly and never accepts a command it cannot act on."), request)
    if not reg.accepts(res.fqn):
        return _rejected(publish.Rejected(
            405, "no-command-lane", "No command lane here",
            f"This endpoint serves {res.fqn} read-only: no backend has "
            f"registered a handler for it, so there is nothing that could act "
            f"on a verb. N.6 makes that a clean refusal rather than a silent "
            f"acceptance.",
            accepts=reg.types()), request)

    who = _writer(request, res.scene)
    if isinstance(who, web.Response):
        return who

    try:
        body = await request.json()
    except web.HTTPRequestEntityTooLarge:
        raise                 # _limited answers it, as the body limit
    except Exception as e:
        return _rejected(publish.Rejected(
            400, "not-json", "Body is not JSON",
            f"The body must be N.1 canonical JSON: {e}"), request)
    try:
        publish.validate_schema(res.fqn, body)
        data, _raw = publish.canonicalize(st, res.fqn, body)
    except publish.Rejected as r:
        return _rejected(r, request)

    handler = reg.handler(res.fqn)
    assert handler is not None          # reg.accepts() was true above
    try:
        outcome = handler(data)
    except Exception as e:
        # A backend that raises is a backend that did not decide. Reporting
        # 500 is honest; reporting a decline would put words in its mouth.
        return _problem(500, "handler-failed", "The command handler failed",
                        f"The backend registered for {res.fqn} raised "
                        f"{type(e).__name__}: {e}",
                        instance=str(request.rel_url))

    if outcome.accepted:
        doc: dict[str, Any] = {"outcome": "accepted", "type": res.fqn}
        if outcome.reason:
            doc["reason"] = outcome.reason
        # Built directly rather than through `_json_response`, which is for
        # resources: it answers 200 and honours If-None-Match, and a command
        # outcome is neither cacheable nor re-fetchable. `no-store` because
        # N.4 forbids any lifetime on an operator-scoped surface.
        return web.Response(body=_body(doc), status=202,
                            content_type="application/json",
                            headers={"Cache-Control": "no-store"})
    return _rejected(publish.Rejected(
        409, "command-declined", "The command was declined",
        outcome.reason, outcome="declined", type=res.fqn), request)


async def put_instance(request: web.Request) -> web.Response:
    """PUT on the same instance URL a GET already serves."""
    if not request.app["publish"]:
        return _disabled(request)
    m = request.match_info
    who = _writer(request, m["scene"])
    if isinstance(who, web.Response):
        return who
    st: gateway.State = request.app["state"]
    try:
        body = await request.json()
    except web.HTTPRequestEntityTooLarge:
        raise                 # _limited answers it, as the body limit
    except Exception as e:
        return _rejected(publish.Rejected(
            400, "not-json", "Body is not JSON",
            f"The body must be N.1 canonical JSON: {e}"), request)
    try:
        topic, res = publish.resource_for_put(
            st, m["scene"], m["stream"], m["type"], m["version"])
        publish.validate_schema(res.fqn, body)
        publish.check_authority(request.app["base"], body)
        data, raw = publish.canonicalize(st, res.fqn, body)
    except publish.Rejected as r:
        return _rejected(r, request)

    key = str(data.get(res.key, "")) if res.key else ""
    want = request.match_info["key"]
    if _bad_key(want):
        return _bad_key_problem(request, want)
    if key != want:
        return _rejected(publish.Rejected(
            400, "key-mismatch", "Body does not match the URL",
            f"This URL addresses the instance keyed {want!r} and the body's "
            f"{res.key} is {key!r}. The instance key is the record's own @key "
            f"member, so the URL cannot rename it.",
            url_key=want, body_key=key), request)

    held = st.latched.get(topic, {}).get(key)
    im = request.headers.get("If-Match")
    # Compared against the ETag this endpoint actually serves. `Instance.etag`
    # is a bare digest and the header form carries the `sha256:` prefix inside
    # quotes, so comparing the two directly never matches -- which made every
    # conditional write a 412, including the correct ones.
    cur = _etag(held.data) if held is not None else ""
    ok_match = im is not None and (
        im.strip() == "*" or im.strip() in (cur, cur.strip('"'), f"W/{cur}"))
    if im and not ok_match:
        return _rejected(publish.Rejected(
            412, "etag-mismatch", "Precondition failed",
            f"If-Match was {im!r} and the instance "
            f"{'does not exist' if held is None else 'is ' + held.etag}. "
            f"An absent If-Match is an unconditional replace.",
            if_match=im), request)

    if held is None:
        over = _over_quota(request, who, res.scene)
        if over is not None:
            return over
    # Journal first: a write the disk refuses is refused to the client, and
    # the registry never holds what its journal does not.
    jr: publish.Journal | None = request.app["journal"]
    if jr is not None:
        try:
            jr.record_sample(topic, res.fqn, key, raw, principal=who.name,
                             scene=res.scene)
        except publish.JournalUnwritable as e:
            return _unwritable(request, e)
    created = publish.apply_sample(st, topic, res, key, data)
    await _broadcast(request, topic,
                     {"event": "sample", "topic": topic, "key": key,
                      "data": data})
    return _written(request, data, created,
                    {"X-SpatialDDS-Topic": topic,
                     "X-SpatialDDS-Type": res.fqn,
                     "X-SpatialDDS-Access": gateway.OPERATOR})


async def delete_instance(request: web.Request) -> web.Response:
    """DELETE is dispose: it leaves the latched set and the event streams."""
    if not request.app["publish"]:
        return _disabled(request)
    m = request.match_info
    who = _writer(request, m["scene"])
    if isinstance(who, web.Response):
        return who
    st: gateway.State = request.app["state"]
    try:
        topic, res = publish.resource_for_put(
            st, m["scene"], m["stream"], m["type"], m["version"])
    except publish.Rejected as r:
        return _rejected(r, request)
    key = m["key"]
    if _bad_key(key):
        return _bad_key_problem(request, key)
    if key not in st.latched.get(topic, {}):
        return _rejected(publish.Rejected(
            404, "no-such-instance", "No such instance",
            f"Topic {topic} holds no instance keyed {key!r}, so there is "
            f"nothing to dispose.", topic=topic, key=key), request)
    reason = request.query.get("reason") or "withdrawn"
    jr: publish.Journal | None = request.app["journal"]
    if jr is not None:
        try:
            jr.record_dispose(topic, key, reason, principal=who.name,
                              scene=m["scene"])
        except publish.JournalUnwritable as e:
            return _unwritable(request, e)
    publish.apply_dispose(st, topic, key)
    await _broadcast(request, topic,
                     {"event": "dispose", "topic": topic, "key": key,
                      "reason": reason})
    return web.Response(status=204,
                        headers=_cache_headers(
                            gateway.OPERATOR, None,
                            request.headers.get("Origin")))


async def put_manifest(request: web.Request) -> web.Response:
    """A content manifest, keyed by the full URI it declares as its own id.

    Published into the same store `--manifests` feeds, so the resolver serves
    it the moment it lands and there is no second mechanism to keep in step.
    """
    if not request.app["publish"]:
        return _disabled(request)
    if request.app["tokens"] and _principal(request) is None:
        return _unauthorized(str(request.rel_url))
    st: gateway.State = request.app["state"]
    try:
        doc = await request.json()
    except web.HTTPRequestEntityTooLarge:
        raise                 # _limited answers it, as the body limit
    except Exception as e:
        return _rejected(publish.Rejected(
            400, "not-json", "Body is not JSON",
            f"The body must be a JSON content manifest: {e}"), request)
    uri = doc.get("id") if isinstance(doc, dict) else None
    if not isinstance(uri, str) or not gateway.uri_authority(uri):
        return _rejected(publish.Rejected(
            400, "no-manifest-id", "Manifest declares no resolvable id",
            f"A manifest is keyed by the full spatialdds:// URI in its own "
            f"`id`, and this body's is {uri!r}."), request)
    try:
        publish.check_authority(request.app["base"], doc)
    except publish.Rejected as r:
        return _rejected(r, request)
    # The manifest's lane is its URI's scene segment, so the resolve surface
    # is enforced by the same rule as the collection paths.
    who = _writer(request, gateway.uri_scene(uri))
    if isinstance(who, web.Response):
        return who
    created = uri not in st.manifests
    if created:
        over = _over_quota(request, who, gateway.uri_scene(uri))
        if over is not None:
            return over
    # Held as its canonical form, the same bytes the journal stores, so the
    # accepting process serves what every later restart will (finding 41:
    # served as sent, `158.0` became `158` at the first restart and the ETag
    # moved with no change of content).
    doc = json.loads(webjson.canon(doc))
    jr = request.app.get("journal")
    if jr is not None:
        try:
            jr.record_manifest(uri, doc, principal=who.name,
                               scene=gateway.uri_scene(uri))
        except publish.JournalUnwritable as e:
            return _unwritable(request, e)
    st.manifests[uri] = doc
    return _written(request, doc, created,
                    {"X-SpatialDDS-Access": gateway.OPERATOR})


async def delete_manifest(request: web.Request) -> web.Response:
    """Withdraw a manifest: the other half of a withdrawal (finding 44).

    `DELETE /spatialdds/resolve?uri=<urlencoded>&reason=<text>`, owner-checked
    by the lane rule: the URI's scene segment must be one the principal may
    write. Afterwards `resolve` answers 404 for that URI. The withdrawal is
    journalled, so it survives a restart, and a seeded manifest withdrawn this
    way stays withdrawn: replay runs after the seed loads.
    """
    if not request.app["publish"]:
        return _disabled(request)
    st: gateway.State = request.app["state"]
    uri = request.query.get("uri", "")
    if not gateway.uri_authority(uri):
        if request.app["tokens"] and _principal(request) is None:
            return _unauthorized(str(request.rel_url))
        return _rejected(publish.Rejected(
            400, "not-a-spatialdds-uri", "Not a resolvable SpatialDDS URI",
            f"DELETE withdraws the manifest named by the `uri` query "
            f"parameter, percent-encoded; {uri!r} is not a spatialdds:// URI."),
            request)
    who = _writer(request, gateway.uri_scene(uri))
    if isinstance(who, web.Response):
        return who
    if uri not in st.manifests:
        return _rejected(publish.Rejected(
            404, "no-such-manifest", "No such manifest",
            f"This endpoint holds no manifest with id {uri!r}, so there is "
            f"nothing to withdraw.", uri=uri), request)
    reason = request.query.get("reason") or "withdrawn"
    jr = request.app.get("journal")
    if jr is not None:
        try:
            jr.record_manifest_withdraw(uri, reason, principal=who.name,
                                        scene=gateway.uri_scene(uri))
        except publish.JournalUnwritable as e:
            return _unwritable(request, e)
    del st.manifests[uri]
    return web.Response(status=204,
                        headers=_cache_headers(
                            gateway.OPERATOR, None,
                            request.headers.get("Origin")))


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
    if res.access == gateway.OPERATOR and not _authorized(request):
        return _unauthorized(str(request.rel_url))

    lim: limits_.Limits = request.app["limits"]
    if lim.ws_open >= lim.max_ws:
        return _limit_problem(
            "too-many-subscriptions",
            f"This endpoint holds at most {lim.max_ws} WebSocket "
            f"subscriptions at once, and that many are open.", request,
            limit_subscriptions=lim.max_ws)
    lim.ws_open += 1
    try:
        return await _subscribe(request, st, topic, res)
    finally:
        lim.ws_open -= 1


async def _subscribe(request: web.Request, st: gateway.State, topic: str,
                     res: gateway.Resource) -> web.StreamResponse:
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

    # The stream is not finished: the socket stays open and carries whatever
    # arrives next -- samples and disposes a publisher sends, or anything an
    # embedded live backend hands to `notify`. It changes the lane in the one
    # direction a subscriber can already handle: more events, same envelope.
    # With it off, everything below runs exactly as before.
    #
    # This was `app["publish"]` until 0.5.0, which meant a live backend had
    # to expose a write surface to keep its subscribers connected. The two
    # switches are separate now and `publish_enabled` still implies this one,
    # so a caller that only ever passed the old flag is unaffected.
    if request.app["live"] and not ws.closed:
        q: asyncio.Queue = asyncio.Queue()
        request.app["subs"].setdefault(topic, set()).add(q)
        try:
            pump = asyncio.create_task(_pump(ws, q))
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
            pump.cancel()
        finally:
            request.app["subs"].get(topic, set()).discard(q)
        return ws

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

async def _pump(ws: web.WebSocketResponse, q: "asyncio.Queue") -> None:
    """Forward broadcast events to one subscriber until it goes away."""
    try:
        while not ws.closed:
            ev = await q.get()
            await ws.send_str(json.dumps(ev, sort_keys=True))
    except (asyncio.CancelledError, ConnectionResetError):
        pass


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


def make_app(state: gateway.State, base: str, token: str | None,
             publish_enabled: bool = False,
             journal: "publish.Journal | None" = None,
             assets: Path | None = None,
             command_registry: "commands.Registry | None" = None,
             live_subscriptions: bool | None = None,
             tokens: "tenancy.TokenTable | None" = None,
             limits: "limits_.Limits | None" = None) -> web.Application:
    """The app, with two switches that used to be one.

    `publish_enabled` means what its name says and nothing else: the
    PUT/DELETE write surface. `live_subscriptions` controls socket lifetime
    --- whether an N.5 subscription stays open after the recorded stream ends,
    waiting for `notify`, instead of sending liveliness-false per key and
    closing.

    They were one flag until 0.5.0, which was a defect found by embedding:
    a backend with its own live source had to switch publishing *on* to keep
    its subscribers connected, which exposed a write surface it did not want.
    Two behaviours under one flag named for the second.

    `live_subscriptions` defaults to `None`, meaning "follow
    `publish_enabled`", so every existing caller behaves exactly as it did ---
    with publishing on, the recording is one producer among others and the
    socket stays open, which is the sentence that was already true. An
    embedder passes `publish_enabled=False, live_subscriptions=True` and gets
    the live lane with no write surface at all.
    """
    lim = limits if limits is not None else limits_.Limits()
    # `client_max_size` one byte past the stated limit is aiohttp's own
    # backstop for a body that declares no length; `_limited` turns its
    # refusal into the same problem document as the declared-length check.
    app = web.Application(middlewares=[_reach, _limited],
                          client_max_size=lim.max_body)
    app["limits"] = lim
    app["state"] = state
    app["base"] = base.rstrip("/")
    app["token"] = token
    # Who may write where. A table from `--tokens` when one is given; else the
    # one `--token`, as a single principal over every scene, which is exactly
    # what that flag meant before tenancy (and nothing gated with neither).
    app["tokens"] = tokens if tokens is not None \
        else tenancy.TokenTable.single(token)
    # Off unless asked for. The write routes are registered either way so
    # that a refusal is an N.8 problem document rather than a framework's
    # default page, and so that the surface's absence is something the
    # endpoint states rather than something a caller infers from a 405.
    app["publish"] = publish_enabled
    app["live"] = publish_enabled if live_subscriptions is None \
        else live_subscriptions
    # The N.6 seam. Empty is the normal state: a gateway serving a recording
    # has no verbs, and N.6 says a suite MUST NOT require a command surface
    # to exist. An embedder registers handlers on this.
    # `is not None`, not `or`. An empty registry is the normal thing to pass:
    # an embedder needs the app before it can build the backend that supplies
    # the handlers, so it passes a registry and populates it a few lines
    # later. With `or` that registry was silently replaced -- `Registry` used
    # to be falsy when empty -- and every later `register()` went to an object
    # the app had already thrown away. Found by the web bridge, whose command
    # lane advertised nothing and refused every POST while its own registry
    # held the handler.
    app["commands"] = (command_registry if command_registry is not None
                       else commands.Registry())
    app["journal"] = journal
    app["subs"] = {}
    #: The serving loop, filled in at startup. `notify` needs it to hand an
    #: event across a thread boundary safely; None until then, which is
    #: correct -- nothing can be subscribed before the app is running.
    app["loop"] = None
    app.on_startup.append(_remember_loop)
    app["assets"] = assets
    app.add_routes([
        web.get("/", root),
        web.get(f"{WELL_KNOWN}/schemas", schemas_index),
        web.get(f"{WELL_KNOWN}/schemas/{{type}}", schema_one),
        web.get(f"{WELL_KNOWN}/search", search),
        web.post(f"{WELL_KNOWN}/search", search),
        web.get(f"{WELL_KNOWN}/bootstrap", no_dds),
        web.get(f"{WELL_KNOWN}/resolver", resolver),
        web.get(f"{WELL_KNOWN}/openapi", openapi),
        web.get(f"{WELL_KNOWN}/openapi.html", openapi_page),
        web.get(gateway.RESOLVE_PATH, resolve),
        web.get(f"{ASSET_PATH}/{{name}}", asset),
        # The latched-resource pattern: the on-bus topic name with
        # `spatialdds/` removed, collection then instance.
        web.get("/{scene}/{stream}/{type}/{version}", collection),
        web.get("/{scene}/{stream}/{type}/{version}/{key}", instance),
        # N.6: a command POST on the lane's own collection URL -- the one
        # the manifest advertises, so a client follows a link rather than
        # building a path. Registered unconditionally, like the write
        # surface below and for the same reason: a refusal should be a stated
        # N.8 problem document, not a framework's default 405 page.
        web.post("/{scene}/{stream}/{type}/{version}", post_command),
        # The write surface, on the same URLs the GETs serve.
        web.put("/{scene}/{stream}/{type}/{version}/{key}", put_instance),
        web.delete("/{scene}/{stream}/{type}/{version}/{key}",
                   delete_instance),
        web.put(gateway.RESOLVE_PATH, put_manifest),
        web.delete(gateway.RESOLVE_PATH, delete_manifest),
        # The preflight, on every path that can be asked for cross-origin
        # with a credential or a JSON body. Mirrors the routes above rather
        # than using a catch-all, so a path this gateway does not serve still
        # 404s on OPTIONS instead of promising a method nobody implements.
        web.options(f"{WELL_KNOWN}/schemas", preflight),
        web.options(f"{WELL_KNOWN}/schemas/{{type}}", preflight),
        web.options(f"{WELL_KNOWN}/search", preflight),
        web.options(f"{WELL_KNOWN}/bootstrap", preflight),
        web.options(f"{WELL_KNOWN}/resolver", preflight),
        web.options(gateway.RESOLVE_PATH, preflight),
        web.options(f"{ASSET_PATH}/{{name}}", preflight),
        web.options("/{scene}/{stream}/{type}/{version}", preflight),
        web.options("/{scene}/{stream}/{type}/{version}/{key}", preflight),
    ])
    return app
