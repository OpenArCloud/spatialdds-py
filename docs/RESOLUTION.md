# Serving `spatialdds://` resolution from a web-binding endpoint

A characterization, not an implementation. The question is whether a web-native
endpoint that already holds content manifests could also answer §7.5 URI
resolution for them, what that would look like on the wire, and what in the
specification has to move first. James decides; nothing here is built.

The motive is concrete: `examples/migration/scd_content.py` resolves a
`spatialdds://` URI by mapping its last path segment to a committed manifest
file. That mapping is a fixture, labelled as one in three places, and
[`gates/content_resolution.py`](../gates/content_resolution.py) carries the same
disclaimer. It exists because resolution is the one link in the OSCP-to-SpatialDDS
chain this repository cannot demonstrate. The example drops the fixture the day
the mechanism lands.

## §7.5 does not need a new sentence. It already requires this.

The expected answer was "§7.5 permits it, and needs a clarifying sentence." The
text says something stronger. §7.5.2 specifies the whole mechanism, normatively
and unconditionally:

- **Resolver metadata.** Each authority MUST expose it at
  `https://{authority}/.well-known/spatialdds/resolver`, with a minimum body of
  `authority`, `https_base`, and `cache_ttl_sec`.
- **Resolve request.** `GET {https_base}?uri={urlencoded SpatialURI}`. The base
  is advertised rather than constructed, so the binding pins no second path.
- **Resolve response.** `200`, the resolved resource body, a correct
  `Content-Type`, and at least one integrity signal — `ETag`, `Digest`, or a
  checksum in the body.
- **Errors.** `400` invalid URI, `404` not found, `401`/`403` unauthorized,
  `5xx` server error (§7.5.3). Clients treat any non-200 as failure.

Nothing in that is DDS-specific, and nothing in it is new surface. It is a
GET with a query parameter returning a JSON document with an ETag, which is
what this endpoint already is.

## What the endpoint would serve

Two resources. Both public-cacheable: a content manifest is a published
description of published content, and `gates/access_classes.py` would gate them
in the existing table rather than a new one.

```
GET /.well-known/spatialdds/resolver
    -> {"authority": "demo.spatialdds.org",
        "https_base": "https://demo.spatialdds.org/spatialdds/resolve",
        "cache_ttl_sec": 300}

GET /spatialdds/resolve?uri=spatialdds%3A%2F%2Fdemo.spatialdds.org%2Fsouth-mall%2Fcontent%2Ffountain-marker
    -> 200, application/json, the content manifest
       ETag: "sha256:…"   the canonical digest, as every resource here carries
```

The integrity signal costs nothing: `gw_http._etag` already computes the digest
of the canonical form for every resource served, which is the same construction
N.2 requires of schemas. The resolver route reuses it unchanged.

Scope would be exactly the manifests the endpoint holds, keyed by the full URI
and not by a segment of it. A URI it does not hold is `404`; a URI whose
authority is not its own is `404` as well, for the reason in the last section.
No construction rule is published, inferred, or inferable — the same discipline
the latched resources follow, where a client reads URLs out of a manifest rather
than assembling them.

## The collision: N.2 and §7.5.2 disagree about this exact path

N.2 lists `/.well-known/spatialdds/resolver` and then says:

> **`bootstrap` and `resolver` are REQUIRED only of an endpoint that offers DDS
> access.** Both exist to hand a client the parameters for joining a domain. A
> web-native endpoint has no domain, no peers and no partitions, and MUST return
> `404` rather than fabricate them.

The second sentence is true of `bootstrap` and false of `resolver`. §7.5.2's
resolver metadata is `authority`, `https_base`, `cache_ttl_sec` — an authority's
URI-resolution entry point. There is no domain in it, no peer list, no
partition. The two paths were grouped by where they sit in the namespace rather
than by what they do.

The consequence is not a gap but a contradiction. For an endpoint that is both
web-native and an authority for `spatialdds://` URIs:

- §7.5.2 says it MUST expose resolver metadata at that path.
- N.2 says it MUST return `404` from that path.

Both are normative. Both cannot be satisfied. Today no endpoint is in that
position, which is why this has stayed invisible — and the moment one is, the
specification tells it to do two incompatible things.

**This does not overturn the earlier ruling.** That ruling — a `404` from these
paths is conformant on a web-native endpoint, and a conformance suite MUST
accept it, because a fabricated `domain_id` is worse than an absence — is right,
and right for the stated reason. It is over-broad by exactly one path. A
web-native endpoint still has no bootstrap parameters, and still must not invent
any. What it may have is manifests, and resolving a URI to a manifest is a
different job that happens to live next door.

*Smallest change:* split the paragraph. `bootstrap` keeps the DDS-access
condition and the `404`-is-conformant ruling verbatim. `resolver` gets its own
sentence: REQUIRED of an endpoint that is an authority for `spatialdds://` URIs
it can resolve, per §7.5.2; `404` is conformant from an endpoint that is not
such an authority, which includes every endpoint that holds no manifests. An
erratum to published text, not a 1.9 feature: no IDL moves and no wire format
changes.

## The second blocker, which is this repository's and not the specification's

Implementing §7.5.2 is necessary for `scd_content.py` to drop its fixture. It is
not sufficient, and this is the part worth deciding before any code is written.

Resolution is DNS-scoped. §7.5.2's metadata path is
`https://{authority}/.well-known/spatialdds/resolver`, so the authority in a URI
*is* the host a client resolves it against. The fountain recording's manifests
are published under:

```
spatialdds://austin.openarcloud.example/south-mall/content/fountain-marker
```

A demo endpoint at `demo.spatialdds.org` is not that authority and must not
answer for it — an endpoint that resolves URIs belonging to a namespace it does
not own is the fabrication N.2 is right to forbid, in a different costume. So
one of three things has to happen, and the choice is James's:

1. **Re-author the recording's URIs** under an authority the endpoint serves, so
   the demo is self-consistent. `tools/author_anchors.py` reads
   `samples/fountain-anchors.input.json` and does no arithmetic on it, so this is
   an input edit and a re-author, with `gates/golden_anchor.py` checking the
   result field by field. It makes the sample less representative of a real
   federated deployment, where content and resolver are routinely different
   hosts.
2. **Serve resolution from the real authority** and leave the demo endpoint out
   of it. Most faithful, and it needs `openarcloud.example` to be a host someone
   operates.
3. **Keep the fixture**, correctly labelled, until a deployment exists where
   authority and endpoint coincide.

Until one of those is chosen, implementing the resolver route would produce an
endpoint that answers `404` for every URI in its own recording — which is
conformant, and no better than the fixture.

## One more thing a resolver would collide with

§7.5.2: "When a fetched manifest carries its own `ttl_sec`, the manifest's
`ttl_sec` governs the cache lifetime of that manifest." The committed content
manifests carry `ttl_sec: 86400`. This gateway serves every public-cacheable
resource with `Cache-Control: public, max-age=300`, from one constant.

So a resolver response here would have to take its `max-age` from the manifest
rather than from the endpoint's default, and a blanket 300 would contradict
§7.5.2 on a resource the endpoint is otherwise serving correctly. N.4 sets cache
behaviour by access class and §7.5.2 sets it by document content; where both
apply, the document wins. That is a second candidate N.4 clarification, recorded
alongside the CORS one in [EVIDENCE.md](EVIDENCE.md).

## What this would cost

Small, and the smallness is the argument for doing it once the authority
question is answered: two routes, one dictionary, and the existing ETag helper.
The gate would be `content_resolution.py` with its fixture replaced by a real
`GET` — the chain it already follows end to end, with one link stopping being a
stand-in. The disclaimer comes out of three files.

## Not proposed

- No resolution of anchor or service URIs. The endpoint holds content manifests;
  anchors cannot carry a `manifest_uri` in 1.8 at all (1.9 candidate 2).
- No `bootstrap`. There is no domain, and that ruling stands.
- No resolver for URIs the endpoint does not hold, and no redirect to one. §7.5.1
  already orders the client's attempts; an endpoint guessing on the client's
  behalf would be inventing a step.
