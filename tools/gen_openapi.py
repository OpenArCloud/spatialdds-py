#!/usr/bin/env python3
"""Generate the OpenAPI document for the REST surface, and its reference page.

    python3 tools/gen_openapi.py           # write both artifacts
    python3 tools/gen_openapi.py --check   # exit 1 if either would change

**Generated, never hand-written.** Every path, verb, status and schema below is
read from the code that serves it, so the document cannot describe a surface
the gateway does not have:

* the type tables -- `gateway.DURABILITY` (what is served, latched or streamed,
  public or operator-scoped) and `publish.PUBLISHABLE` (the three writable
  kinds, whose type segment is fixed by that table);
* the published JSON Schemas under `artifacts/schemas`, hoisted into
  `components.schemas` with their `$defs` (a definition two schemas disagree
  about stops generation rather than picking one);
* the write rule, `tenancy.WRITE_REFUSALS`, which is the same table the
  gateway refuses from;
* the well-known paths of Appendix N.2 and this endpoint's own routes, as
  `gw_http` names them.

`gates/openapi.py` regenerates and diffs: a changed table row, schema or write
rule that does not change the committed document fails the gate.

**The WebSocket surface is described, not modelled.** OpenAPI has no honest
shape for N.5's stream (latched replay, one `end_of_replay`, then live), so
each collection path says in prose that the same URL upgrades, summarizes the
envelope, and links N.5. AsyncAPI is the format for that surface if anyone
ever needs it formally; nothing here builds one.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import yaml                                                    # noqa: E402

from spatialdds.server import gateway, gw_http, limits, publish, tenancy  # noqa: E402

ART = ROOT / "lib" / "spatialdds" / "spatialdds" / "artifacts"
OUT_YAML = ART / "openapi.yaml"
OUT_HTML = ART / "openapi.html"
#: The server URL in the committed document. The gateway serves the document
#: with its own base substituted for this one line's value.
DEFAULT_BASE = "https://demo.spatialdds.org"
N5 = "https://spatialdds.org/v1.8/appendix-n/"


def snake_ref(fqn: str) -> str:
    return "#/components/schemas/" + fqn.replace(".", "_")


def components() -> dict:
    """Every published schema, its `$defs` hoisted and its refs rewritten.

    A type appears once as its own file (carrying `title`, the dotted FQN) and
    again as a `$defs` copy inside every schema that uses it (without one).
    Measured over the 112 published files: the copies differ from the root in
    `title` and in nothing else. So they are compared without `title`, the
    titled root is what is kept, and any other difference stops generation --
    two schemas disagreeing about a type is a defect to report, not a choice
    to make here.
    """
    roots: dict[str, dict] = {}
    copies: dict[str, tuple[str, dict]] = {}
    for p in sorted((ART / "schemas").glob("*.json")):
        doc = json.loads(p.read_text())
        defs = doc.pop("$defs", {})
        doc.pop("$schema", None)
        doc.pop("$id", None)
        roots[p.stem] = _rewrite(doc)
        for name, d in sorted(defs.items()):
            d = _rewrite(d)
            if name in copies and _untitled(copies[name][1]) != _untitled(d):
                raise SystemExit(f"{p.name}: $defs/{name} differs from the "
                                 f"copy in {copies[name][0]}; the published "
                                 f"schemas disagree about one type")
            copies.setdefault(name, (p.name, d))
    for name, (where, d) in copies.items():
        if name in roots and _untitled(roots[name]) != _untitled(d):
            raise SystemExit(f"{where}: $defs/{name} differs from "
                             f"{name}.json itself")
    return dict(sorted({**{n: d for n, (_w, d) in copies.items()},
                        **roots}.items()))


def _untitled(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "title"}


def _rewrite(node):
    """`$defs` refs to `components`, and every `const` given its `enum` twin.

    The published schemas tag each union branch with `{"const": "COV_POS3"}`,
    which is correct JSON Schema 2020-12 and correct OpenAPI 3.1. Measured
    against openapi-generator 7.10.0's Python client, it is not enough: that
    generator ignores `const`, so every branch of `CovMatrix` matched a
    GeoAnchor's `cov` and the read flow failed "Multiple matches found". A
    one-value `enum` says the same thing in a form 3.0-era tooling reads, so
    it is added beside the `const` (never instead of it) in this document
    only; the published schemas are not touched.
    """
    if isinstance(node, dict):
        out = {k: (v.replace("#/$defs/", "#/components/schemas/")
                   if k == "$ref" and isinstance(v, str) else _rewrite(v))
               for k, v in node.items()}
        if "const" in out and "enum" not in out:
            out["enum"] = [out["const"]]
        return out
    if isinstance(node, list):
        return [_rewrite(v) for v in node]
    return node


def refusals(*slugs: str) -> dict:
    out = {}
    for slug in slugs:
        status, title, when = tenancy.WRITE_REFUSALS[slug]
        key = str(status)
        prev = out.get(key, {}).get("description", "")
        line = f"`{slug}` -- {title}: {when}."
        out[key] = {"description": (prev + " " if prev else "") + line,
                    "content": {gw_http.PROBLEM: {"schema": {
                        "$ref": "#/components/schemas/Problem"}}}}
    return out


def limit(*slugs: str) -> dict:
    """The stated limits a path can hit, from `limits.REFUSALS`."""
    out = {}
    for slug in slugs:
        status, title, when = limits.REFUSALS[slug]
        out.update(problem(status, f"`{slug}` -- {title}: {when}."))
    return out


WRITE_LIMITS = ("body-too-large", "rate-limited")


def problem(status: int, why: str) -> dict:
    return {str(status): {"description": why, "content": {gw_http.PROBLEM: {
        "schema": {"$ref": "#/components/schemas/Problem"}}}}}


def ws_note(fqn: str | None) -> str:
    return (f"**The same URL is also a WebSocket** (N.5): connect with "
            f"`wss://` and receive the latched set as `sample` events, exactly "
            f"one `end_of_replay` carrying the replay count, then live `sample` "
            f"and `dispose` events (`{{event, topic, key, data | reason}}`). "
            f"Not modelled here; see [Appendix N.5]({N5}).")


SCENE_RULE = ("**Writes are per lane.** The path's first segment is the scene; "
              "the bearer token must be granted it (`tenancy.WRITE_REFUSALS`). "
              "Every member of the record is required: a body is a whole "
              "record, never a patch.")

PARAM = {
    "scene": {"name": "scene", "in": "path", "required": True,
              "description": "The §3.3.1 domain segment; on a multi-tenant "
                             "registry, a lane.",
              "schema": {"type": "string"}},
    "stream": {"name": "stream", "in": "path", "required": True,
               "description": "The §3.3.1 stream segment (e.g. `content`, "
                              "`discovery`, `frames`), as the search "
                              "manifest's topic URL gives it.",
               "schema": {"type": "string"}},
    "type": {"name": "type", "in": "path", "required": True,
             "description": "The §3.3.1 type segment; the response's "
                            "`X-SpatialDDS-Type` header names its schema.",
             "schema": {"type": "string"}},
    "version": {"name": "version", "in": "path", "required": True,
                "schema": {"type": "string"}},
    "key": {"name": "key", "in": "path", "required": True,
            "description": "The record's own `@key` member, percent-encoded "
                           "as one segment (`/` is `%2F`).",
            "schema": {"type": "string"}},
}
ETAG = {"ETag": {"schema": {"type": "string"},
                 "description": "Revalidate with `If-None-Match`; `304` when "
                                "unchanged."}}


def opname(seg: str) -> str:
    return "".join(w.capitalize() for w in seg.split("_"))


def write_paths(seg: str, fqn: str, latched: bool, access: str) -> dict:
    coll = f"/{{scene}}/{{stream}}/{seg}/v1"
    inst = coll + "/{key}"
    ref = {"$ref": snake_ref(fqn)}
    name = opname(seg)
    read_sec = [] if access == gateway.PUBLIC else [{"bearer": []}]
    return {
        coll: {
            "parameters": [{"$ref": "#/components/parameters/scene"},
                           {"$ref": "#/components/parameters/stream"}],
            "get": {
                "operationId": f"list{name}", "tags": [seg],
                "summary": f"Every {fqn} instance in the latched set",
                "description": ws_note(fqn),
                "security": read_sec,
                "responses": {
                    "200": {"description": "The latched set.",
                            "headers": ETAG,
                            "content": {"application/json": {"schema": {
                                "type": "array", "items": ref}}}},
                    "304": {"description": "Not modified."},
                    **problem(404, "No such resource on this endpoint."),
                }},
            "post": {
                "operationId": f"command{name}", "tags": [seg],
                "summary": f"An N.6 command addressed through the {seg} lane",
                "description": "Accepted only on a lane whose topic advertises "
                               "`accepts_commands`; otherwise `405`. "
                               + SCENE_RULE,
                "security": [{"bearer": []}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": ref}}},
                "responses": {
                    "202": {"description": "Accepted (not completed)."},
                    **problem(400, "Body not valid for the type."),
                    **refusals(*tenancy.WRITE_REFUSALS),
                    **problem(405, "No command lane here."),
                    **problem(409, "Declined by the backend, with a reason."),
                }},
        },
        inst: {
            "parameters": [{"$ref": "#/components/parameters/scene"},
                           {"$ref": "#/components/parameters/stream"},
                           {"$ref": "#/components/parameters/key"}],
            "get": {
                "operationId": f"get{name}", "tags": [seg],
                "summary": f"One {fqn} instance",
                "security": read_sec,
                "responses": {
                    "200": {"description": "The instance.", "headers": ETAG,
                            "content": {"application/json": {"schema": ref}}},
                    "304": {"description": "Not modified."},
                    **problem(404, "No such instance."),
                }},
            "put": {
                "operationId": f"put{name}", "tags": [seg],
                "summary": f"Publish or replace a {fqn}",
                "description": SCENE_RULE + " The body's key must equal the "
                               "URL's. `If-Match` is honoured when present.",
                "security": [{"bearer": []}],
                "parameters": [{"name": "If-Match", "in": "header",
                                "required": False,
                                "schema": {"type": "string"}}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": ref}}},
                "responses": {
                    "200": {"description": "Replaced.", "headers": ETAG,
                            "content": {"application/json": {"schema": ref}}},
                    "201": {"description": "Created.", "headers": ETAG,
                            "content": {"application/json": {"schema": ref}}},
                    **problem(400, "Not JSON, not valid for the schema, not "
                                   "encodable, the key differs from the URL, "
                                   "or the key has a control character "
                                   "(`bad-key`)."),
                    **refusals(*tenancy.WRITE_REFUSALS),
                    **problem(405, "Publishing is off on this endpoint."),
                    **problem(412, "If-Match did not match."),
                }},
            "delete": {
                "operationId": f"withdraw{name}", "tags": [seg],
                "summary": f"Withdraw (dispose) a {fqn}",
                "description": SCENE_RULE.split(" Every member")[0] + " The "
                               "dispose streams on the collection's WebSocket "
                               "with its `reason`.",
                "security": [{"bearer": []}],
                "parameters": [{"name": "reason", "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                                "description": "Carried on the dispose "
                                               "event; `withdrawn` if absent."}],
                "responses": {
                    "204": {"description": "Withdrawn."},
                    **problem(400, "`bad-key` -- a key with a control "
                                   "character."),
                    **refusals(*tenancy.WRITE_REFUSALS),
                    **problem(404, "No such instance."),
                    **problem(405, "Publishing is off on this endpoint."),
                }},
        },
    }


def read_paths() -> dict:
    served = sorted(set(gateway.DURABILITY) - set(publish.PUBLISHABLE.values()))
    one_of = {"oneOf": [{"$ref": snake_ref(f)} for f in served]}
    return {"/{scene}/{stream}/{type}/{version}": {
        "parameters": [{"$ref": f"#/components/parameters/{n}"}
                       for n in ("scene", "stream", "type", "version")],
        "get": {
            "operationId": "listInstances", "tags": ["read-only"],
            "summary": "Any other served topic, read-only",
            "description": "Latched types answer with the latched set; "
                           "streamed types with the replayed window. Public "
                           "or operator-scoped per the type: "
                           + ", ".join(f"`{f}` {'latched' if l else 'stream'} "
                                       f"{'public' if a == gateway.PUBLIC else 'bearer'}"
                                       for f, (l, a) in sorted(
                                           gateway.DURABILITY.items())
                                       if f in served)
                           + ". " + ws_note(None),
            "security": [{}, {"bearer": []}],
            "responses": {
                "200": {"description": "The instances; `X-SpatialDDS-Type` "
                                       "names which schema.",
                        "headers": {**ETAG, "X-SpatialDDS-Type": {
                            "schema": {"type": "string"}}},
                        "content": {"application/json": {"schema": {
                            "type": "array", "items": one_of}}}},
                "304": {"description": "Not modified."},
                **problem(401, "An operator-scoped type, without a token."),
                **problem(404, "No such resource on this endpoint."),
            }}}}


def short_paths() -> dict:
    """N.3's four-segment topics, rooted in the well-known namespace (1.8 stamp)."""
    wk = gw_http.WELL_KNOWN
    seg = [{"$ref": f"#/components/parameters/{n}"}
           for n in ("stream", "type", "version")]
    note = ("A four-segment topic (`spatialdds/<stream>/<type>/<version>`, no "
            "scene), such as the discovery announce lane, served exactly as a "
            "scene-scoped collection is: latched set, `ETag`, `304`, the same "
            "access classes, and the N.5 stream by upgrading the same path. "
            "Read-only: a topic with no scene is in no lane. ")
    ok = {"200": {"description": "As for a scene-scoped topic.",
                  "headers": ETAG,
                  "content": {"application/json": {"schema": {}}}},
          "304": {"description": "Not modified."},
          **problem(401, "An operator-scoped type, without a token."),
          **problem(404, "No such topic, or no such instance.")}
    return {
        f"{wk}/{{stream}}/{{type}}/{{version}}": {
            "parameters": seg,
            "get": {"operationId": "listWellKnownInstances",
                    "tags": ["read-only"],
                    "summary": "A four-segment topic's collection",
                    "description": note + ws_note(None),
                    "security": [{}, {"bearer": []}], "responses": ok}},
        f"{wk}/{{stream}}/{{type}}/{{version}}/{{key}}": {
            "parameters": seg + [{"$ref": "#/components/parameters/key"}],
            "get": {"operationId": "getWellKnownInstance",
                    "tags": ["read-only"],
                    "summary": "One instance of a four-segment topic",
                    "description": note,
                    "security": [{}, {"bearer": []}], "responses": ok}},
    }


def well_known() -> dict:
    wk = gw_http.WELL_KNOWN
    obj = {"type": "object", "additionalProperties": True}
    return {
        f"{wk}/search": {
            "get": {
                "operationId": "search", "tags": ["discovery"],
                "summary": "Services near a geohash (§8.2.3 manifests)",
                "parameters": [{"name": "geohash", "in": "query",
                                "required": False,
                                "schema": {"type": "string"}}],
                "responses": {"200": {
                    "description": "`{results: [{service: {service_id, kind, "
                                   "topics: [{type, url, durability, …}]}}], "
                                   "next_page_token}`; follow each topic's "
                                   "`url`, never build one.",
                    "content": {"application/json": {"schema": {
                        "type": "object", "properties": {
                            "results": {"type": "array", "items": obj},
                            "next_page_token": {"type": "string"}},
                        "required": ["results"]}}}}}},
            "post": {
                "operationId": "searchPost", "tags": ["discovery"],
                "summary": "The same search, as a POST body",
                "requestBody": {"content": {"application/json": {
                    "schema": obj}}},
                "responses": {"200": {"description": "As GET.",
                                      "content": {"application/json": {
                                          "schema": obj}}}}}},
        f"{wk}/schemas": {"get": {
            "operationId": "listSchemas", "tags": ["discovery"],
            "summary": "The published JSON Schemas, by type",
            "responses": {"200": {"description": "`[{type, url, digest}]`.",
                                  "content": {"application/json": {"schema": {
                                      "type": "array", "items": obj}}}}}}},
        f"{wk}/schemas/{{type}}": {"get": {
            "operationId": "getSchema", "tags": ["discovery"],
            "parameters": [{"name": "type", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "responses": {"200": {"description": "One JSON Schema.",
                                  "content": {"application/json": {
                                      "schema": obj}}},
                          **problem(404, "No such type.")}}},
        f"{wk}/bootstrap": {"get": {
            "operationId": "bootstrap", "tags": ["discovery"],
            "summary": "DDS-join material (N.2); optional",
            "responses": {"200": {"description": "Bootstrap material.",
                                  "content": {"application/json": {
                                      "schema": obj}}},
                          **problem(404, "This endpoint offers no DDS join.")}}},
        f"{wk}/resolver": {"get": {
            "operationId": "resolverMetadata", "tags": ["resolution"],
            "summary": "§7.5.2 resolver metadata",
            "responses": {"200": {"description": "`{authority, https_base, "
                                                 "…}`.", "headers": ETAG,
                                  "content": {"application/json": {
                                      "schema": obj}}},
                          **problem(404, "Not an authority for any URI.")}}},
        f"{wk}/openapi": {"get": {
            "operationId": "openapi", "tags": ["discovery"],
            "summary": "This document",
            "responses": {"200": {"description": "OpenAPI 3.1, YAML.",
                                  "content": {"application/yaml": {
                                      "schema": {"type": "string"}}}}}}},
        f"{wk}/openapi.html": {"get": {
            "operationId": "openapiPage", "tags": ["discovery"],
            "summary": "This document as a static reference page",
            "responses": {"200": {"description": "HTML, no script.",
                                  "content": {"text/html": {
                                      "schema": {"type": "string"}}}}}}},
        gateway.RESOLVE_PATH: {
            "parameters": [{"name": "uri", "in": "query", "required": True,
                            "description": "A `spatialdds://` URI, "
                                           "percent-encoded.",
                            "schema": {"type": "string"}}],
            "get": {
                "operationId": "resolve", "tags": ["resolution"],
                "summary": "Resolve a URI to its manifest (§7.5.2)",
                "responses": {"200": {"description": "The N.7 manifest.",
                                      "headers": ETAG,
                                      "content": {"application/json": {
                                          "schema": obj}}},
                              **problem(400, "Not a SpatialDDS URI."),
                              **problem(404, "Another authority's, or not "
                                             "held.")}},
            "put": {
                "operationId": "putManifest", "tags": ["resolution"],
                "summary": "Publish a manifest, keyed by its own `id`",
                "description": "The `uri` parameter is not used; the body's "
                               "`id` is the key. Its scene segment is the "
                               "lane. " + SCENE_RULE.split(" Every")[0],
                "security": [{"bearer": []}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": obj}}},
                "responses": {"200": {"description": "Replaced."},
                              "201": {"description": "Created."},
                              **problem(400, "No resolvable `id`."),
                              **refusals(*tenancy.WRITE_REFUSALS),
                              **problem(405, "Publishing is off.")}},
            "delete": {
                "operationId": "withdrawManifest", "tags": ["resolution"],
                "summary": "Withdraw a manifest; `resolve` then answers 404",
                "description": "Owner-checked by the URI's scene segment. "
                               + SCENE_RULE.split(" Every")[0],
                "security": [{"bearer": []}],
                "parameters": [{"name": "reason", "in": "query",
                                "required": False,
                                "schema": {"type": "string"}}],
                "responses": {"204": {"description": "Withdrawn."},
                              **problem(400, "Not a SpatialDDS URI."),
                              **refusals(*tenancy.WRITE_REFUSALS),
                              **problem(404, "Not held."),
                              **problem(405, "Publishing is off.")}}},
        f"{gw_http.ASSET_PATH}/{{name}}": {"get": {
            "operationId": "getAsset", "tags": ["assets"],
            "summary": "Asset bytes this endpoint hosts (implementation-"
                       "defined)",
            "parameters": [{"name": "name", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "responses": {"200": {"description": "The bytes; check them "
                                                 "against the manifest's "
                                                 "hash.",
                                  "content": {"application/octet-stream": {
                                      "schema": {"type": "string",
                                                 "format": "binary"}}}},
                          **problem(404, "Not hosted here.")}}},
    }


def document() -> dict:
    import spatialdds
    schemas = components()
    schemas["Problem"] = {
        "description": "RFC 7807 problem document (Appendix N.8). Members "
                       "beyond the five are extension members.",
        "type": "object", "additionalProperties": True,
        "properties": {"type": {"type": "string"},
                       "title": {"type": "string"},
                       "status": {"type": "integer"},
                       "detail": {"type": "string"},
                       "instance": {"type": "string"}},
        "required": ["type", "title", "status", "detail"]}
    paths = well_known()
    for seg, fqn in sorted(publish.PUBLISHABLE.items()):
        latched, access = gateway.DURABILITY.get(fqn, (True, gateway.PUBLIC))
        paths.update(write_paths(seg, fqn, latched, access))
    paths.update(read_paths())
    paths.update(short_paths())
    # The stated limits, on every operation they can refuse, from the same
    # table the gateway refuses from: the rate on everything, the body limit
    # on everything that takes one, the subscription cap on every path that
    # upgrades to a WebSocket.
    for path, item in paths.items():
        for verb in ("get", "put", "post", "delete"):
            op = item.get(verb)
            if not op:
                continue
            extra = ["rate-limited"]
            if "requestBody" in op:
                extra += ["body-too-large", "unsupported-media-type"]
            if verb == "get" and "WebSocket" in op.get("description", ""):
                extra.append("too-many-subscriptions")
            for k, v in limit(*extra).items():
                op["responses"].setdefault(k, v)
            if verb in ("put", "delete") and "security" in op \
                    and op["security"] == [{"bearer": []}]:
                op["responses"].setdefault("503", problem(
                    503, "`journal-unwritable` -- the journal could not be "
                         "written (disk full); nothing was accepted, reads "
                         "are unaffected, retry after `Retry-After`.")["503"])
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "SpatialDDS web binding (spatialdds-py)",
            "version": spatialdds.__version__,
            "summary": "SpatialDDS 1.8 Appendix N over HTTPS, plus this "
                       "endpoint's write surface.",
            "description": "Generated by `tools/gen_openapi.py` from the "
                           "gateway's own tables and the published JSON "
                           "Schemas; never edited by hand. Reads are open; "
                           "writes take a bearer token scoped to scenes. "
                           "The WebSocket streams (N.5) are described on each "
                           "collection path, not modelled.",
        },
        "servers": [{"url": DEFAULT_BASE}],
        "tags": [{"name": t} for t in
                 ["discovery", "resolution", "assets",
                  *sorted(publish.PUBLISHABLE), "read-only"]],
        "security": [{}],
        "paths": paths,
        "components": {
            "securitySchemes": {"bearer": {
                "type": "http", "scheme": "bearer",
                "description": "A token from the registry's token table, "
                               "scoped to scenes. Never in a URL."}},
            "parameters": PARAM,
            "responses": {"Problem": {
                "description": "A problem document.",
                "content": {gw_http.PROBLEM: {"schema": {
                    "$ref": "#/components/schemas/Problem"}}}}},
            "schemas": schemas,
        },
    }


def to_yaml(doc: dict) -> str:
    return ("# Generated by tools/gen_openapi.py -- do not edit; regenerate.\n"
            + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                             width=100000, default_flow_style=False))


def to_html(doc: dict) -> str:
    """A static reference page: no script, no CDN, nothing to run."""
    e = html.escape
    md = lambda s: re.sub(r"`([^`]+)`", r"<code>\1</code>", e(s or ""))  # noqa: E731
    rows = []
    for path, item in doc["paths"].items():
        for verb in ("get", "put", "post", "delete"):
            op = item.get(verb)
            if not op:
                continue
            auth = "token" if op.get("security") == [{"bearer": []}] else (
                "open" if op.get("security", doc["security"]) in ([{}], [])
                else "token for operator-scoped")
            codes = " ".join(f'<span class="s s{c[0]}">{c}</span>'
                             for c in op["responses"])
            rows.append(
                f'<section id="{e(op["operationId"])}"><h3><span class="v '
                f'{verb}">{verb.upper()}</span> <code>{e(path)}</code></h3>'
                f'<p class="sum">{md(op.get("summary", ""))} '
                f'<span class="auth">{auth}</span></p>'
                + (f'<p>{md(op["description"])}</p>' if op.get("description")
                   else "")
                + f'<p class="codes">{codes}</p></section>')
    css = """
:root{--bg:#fbfbfa;--fg:#1f2328;--mut:#5b636d;--line:#e3e5e8;--acc:#0b6e69;
--code:#f1f2f1;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#121516;--fg:#e6e8ea;--mut:#9aa3ab;
--line:#2a2f33;--acc:#5fc2bb;--code:#1c2124;color-scheme:dark}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,
-apple-system,Segoe UI,sans-serif}main{max-width:62rem;margin:0 auto;
padding:2rem 1rem 4rem}h1{font-size:1.6rem;margin:0 0 .3rem}h2{font-size:1.1rem;
margin:2.2rem 0 .6rem;border-bottom:1px solid var(--line);padding-bottom:.3rem}
h3{font-size:.98rem;margin:0;font-weight:600;overflow-wrap:anywhere}
code{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:
var(--code);padding:.05rem .3rem;border-radius:3px}section{padding:.9rem 0;
border-bottom:1px solid var(--line)}p{margin:.35rem 0}.mut,.sum .auth{color:
var(--mut)}.auth{font-size:.85rem;margin-left:.4rem}.v{display:inline-block;
min-width:4.2rem;font:600 12px ui-monospace,monospace;letter-spacing:.04em;
color:var(--acc)}.codes{font:12px ui-monospace,monospace;color:var(--mut)}
.s{margin-right:.5rem}.s4,.s5{color:#b5482c}a{color:var(--acc)}"""
    info = doc["info"]
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(info['title'])}</title><style>{css}</style></head>"
            f"<body><main><h1>{e(info['title'])}</h1>"
            f"<p class=mut>Version {e(info['version'])} · OpenAPI "
            f"{e(doc['openapi'])} · generated, never hand-written · "
            f"<a href='openapi'>openapi.yaml</a></p>"
            f"<p>{md(info['description'])}</p>"
            f"<h2>Operations</h2>{''.join(rows)}"
            f"<h2>Schemas</h2><p class=mut>{len(doc['components']['schemas'])} "
            f"components, from the published JSON Schemas at "
            f"<code>/.well-known/spatialdds/schemas</code>.</p>"
            f"</main></body></html>\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    doc = document()
    want = {OUT_YAML: to_yaml(doc), OUT_HTML: to_html(doc)}
    stale = [p for p, text in want.items()
             if not p.is_file() or p.read_text() != text]
    if a.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for p, text in want.items():
        p.write_text(text)
        print(f"wrote {p.relative_to(ROOT)} ({len(text)} bytes)")
    print(f"{len(doc['paths'])} paths, "
          f"{sum(1 for i in doc['paths'].values() for v in i if v in ('get','put','post','delete'))} "
          f"operations, {len(doc['components']['schemas'])} schemas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
