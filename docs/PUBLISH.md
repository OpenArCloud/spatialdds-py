# The publish surface

Services and content can be published to this endpoint over HTTP. **This is an
implementation-defined endpoint extension, not a reading of SpatialDDS 1.8.**

1.8 specifies no publish verb and forbids none. So nothing here is proposed as
specification text by erratum — an erratum fixes a contradiction, and this is a
feature. It is written up as 1.9 candidate entry 4, with this implementation as
its first evidence. Until then the surface is labeled wherever it appears, and
two properties keep the endpoint conformant with it enabled:

- A read-only client cannot tell a published instance from a replayed one. Not
  by promise: a published body is encoded to XCDR2 with the `Writer` validated
  byte-identically against CycloneDDS, then decoded and projected back through
  the same `Decoder` and `Projector` the serving path uses. What is stored is
  the output of that round trip, so the canonical form is the only
  representation the surface can produce. `gates/golden_publish.py` checks a
  published record against the same assertions a replayed one passes.
- The conformance suite never touches these routes, and
  `gates/publish_offswitch.py` checks that with the surface off every read is
  byte-identical to an endpoint built without it and the write verbs are
  refused rather than merely unused.

It is **off by default**. `deploy/` does not enable it.


## From the front page: one PUT, and the record is in the registry

Moved here on 2026-10-16, intact, when the front page became pip-first. The
`curl` below is still executed by `gates/examples.py` on every run, which is
why this document is one of the files that gate reads.

Services and content can be published over HTTP: `PUT` on the same instance URL
a `GET` serves, `DELETE` to withdraw. **The surface is implementation-defined
and off by default** -- 1.8 specifies no publish verb and forbids none, so this
is an endpoint extension, not a reading of the specification. A read-only client
cannot tell a published record from a replayed one, which is the property that
keeps the endpoint conformant either way. The rest of
this page is the detail.

Serve it with the surface on, as its own authority, and it needs a token:

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 \
    --public-url https://demo.spatialdds.org --token tok --publish \
    --journal /tmp/registry.mcap
```

Then one PUT, and the record is in the registry the read path serves:

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service"]["record"]))' \
    | curl -s -X PUT "$BASE/ut-austin-south-mall/discovery/announce/v1/austin-vps" \
        -H "Authorization: Bearer tok" -H "Content-Type: application/json" \
        --data @- \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("published", d["service_id"], "|", d["name"])'
```

```console
published austin-vps | Austin VPS, published over HTTP
```

[`publish_service.py`](../examples/publish_service.py),
[`publish_content.py`](../examples/publish_content.py) and
[`publish_duck.py`](../examples/publish_duck.py) each do a whole round trip in one
file — publish, then read back through search, the resolver, and for the duck
the asset's bytes.

Two walked documents cover the same ground from both ends:
[docs/MYTOWN.md](MYTOWN.md) stands a registry up and populates it with
the library in eight steps, and [docs/WALKTHROUGH.md](WALKTHROUGH.md)
does a VPS and a duck with `curl` and no library at all. Both are executed by
`gates/quickstart.py` exactly as printed.

## The verbs

| | |
| --- | --- |
| `PUT /{domain}/{stream}/{type}/{version}/{key}` | publish an instance: `201` created, `200` replaced |
| `DELETE /{domain}/{stream}/{type}/{version}/{key}` | dispose it: `204`, and the dispose streams on the topic's WS |
| `PUT /spatialdds/resolve` | publish a content manifest, keyed by the `id` it declares |
| `DELETE /spatialdds/resolve?uri=…&reason=…` | withdraw that manifest: `204`, and `resolve` answers `404` for it |

The instance key is the record's own `@key` member, percent-encoded as one path
segment exactly as a GET requires, and a body whose key disagrees with the URL
is a `400`: a URL cannot rename a record.

The response body is the served canonical form and carries the instance's
`ETag`, so a publisher holds what every reader will see without a second
request. Writes are operator-scoped, so responses are `no-store`.

`If-Match` is honoured when present — a mismatch is `412`, and `*` means "any
existing". **An absent `If-Match` is an unconditional replace.** That is a
choice rather than a reading, and it is recorded in the candidates entry as an
open question for 1.9 rather than settled here forever.

Three record kinds are publishable: `announce`, `content_announce`, and
`geo_anchor`. Since 0.8.0 so is the world model's `entity` lane, and any keyed
type the deployment registers (`serve --extension`); those are the **world
lanes**, below. Everything else this endpoint serves stays read-only, and a
`PUT` to any other topic is a `405` naming what is publishable. Every
publishable type requires every member, so a body is a whole record and never
a patch; there is no `PATCH`.

### What publishing an anchor means

Mechanically an anchor publish is the third row in a table: the same routes,
token, validation, journal, WebSocket events and `If-Match` as the other two,
and no new routes. Three things are worth saying plainly, because the mechanics
do not say them.

**Method is the claim level.** A published anchor asserts a pose at exactly the
confidence its own `method` and `cov` declare — `Surveyed`, `VisualFix`,
`GNSS`, `Fusion`. Publishing does not upgrade a claim into surveyed truth. The
record's own evidence fields are the reader's whole basis, and
`gates/publish_anchor.py` checks that `confidence` really is the monotone
summary of `cov` that `GeoAnchor` asks for, so the two cannot contradict each
other in a record this endpoint accepted.

**Content linkage is real now, and goes exactly as far as specified.** The 1.8
revision (spec `297c433`) gave `GeoAnchor` an optional `manifest_uri`, so a
published anchor may name a content manifest the way the fountain's plaque
anchor does. Content named that way inherits the anchor's frame and precision —
its `method` and `cov` are the claim level the binding carries — and that is
all the revision says. An anchor whose `manifest_uri` names an authority this
endpoint does not serve gets the same `403` as any other foreign-authority URI;
that leg of the authority check had no data to exercise it until the revision
gave anchors a URI to carry.

**Provenance is deferred with the rest of identity.** Who published an anchor,
and whether it refines an earlier one, are the per-provider-token and
server-set-identity questions already on the deferred list below; anchors
joined the write surface under the same deferral rather than ahead of it. Worth
observing once: a self-describing anchor published to a web registry, able to
bind content at its own stated precision, is the open-anchors direction taking
its first real step — which is also why the deferred provenance questions stop
being theoretical.

## World lanes: entities and claims (0.8.0)

An HTTP client can put living things into a world, not only records about one.
The `entity` lane (`spatial.owm.Entity`) and a deployment's own keyed types
take the same `PUT`/`DELETE`, scene check, schema validation, wire round trip,
quota and journal as the record lanes. What is new is what a write promises.

**Basis gates liveliness.** A DDS writer's liveliness says whether anybody is
still asserting what it wrote; an HTTP client has none, and the gateway does
not fake one. So:

- A `DECLARED` or `AUTHORED` body is a declaration. It holds until withdrawn,
  like a record, and a `?refresh=` on it is refused (`refresh-not-applicable`).
- An `OBSERVED` or `DERIVED` body says somebody is watching. It must declare
  `?refresh=<seconds>` (1 to 3600) and be `PUT` again within that interval; a
  re-PUT starts the interval again. With no interval it is refused
  (`refresh-required`, `rule: basis`), and out of range as `refresh-invalid`.
- When an interval lapses the gateway withdraws the instance itself: a terminal
  sample (`state` RETIRED, `state_reason` `refresh lapsed`, `stamp` now) then
  the dispose with the same reason. The gateway owns the writer, so this is the
  writer's own deliberate removal (§2.14), never a disposal synthesized on
  somebody else's behalf. The journal attributes it to `spatialdds-gateway`.

A `DELETE` on a world lane is the same shape with your `?reason=`: terminal
sample, then dispose. The record lanes still dispose alone, unchanged.

The journal records each observed write's interval, so a restarted gateway
re-arms the lease from its restart: a client that stops refreshing while the
gateway is down still lapses, one interval after it comes back.

**A claim's asserter is the token's principal.** A type that names an
`asserter` (the 0.2 design's Relationship, or any deployment type spelled the
same way) has it grounded the way a bus claim is grounded in its participant:
an empty asserter is stamped with the principal's name, and any other value
must be that name or the `PUT` is a `403` `asserter-not-principal` naming
both. A body with no `asserter` member at all is a body the schema rejects,
and is refused as one (`400`) before anything is stamped. An endpoint with no token table has no principal and refuses claims
(`asserter-ungrounded`). A single `--token` is the principal `operator`.

**A deployment's own types.** `serve --extension MODULE` imports a module that
calls `sd.dds.extend()`; each keyed type it registers is served and publishable
on its own topic, validated against the schema N.2 publishes for it. It is a
world lane if it has `basis`, `state`, `state_reason` and `stamp`.

**On a bus.** `serve --dds-domain N` joins a DDS domain, and what the world
lanes accept is written there too, from the gateway's own participant
(`svc:spatialdds:gateway`), latched, so DDS readers see ordinary samples; a
lapse or a `DELETE` is the SDK's own `Writer.withdraw()` on the bus. A world
lane the gateway cannot write to its bus is refused `503` `not-on-bus` before
anything is journalled. On start the gateway republishes what its journal
holds. The record lanes are not written to the bus. See
[WIRE.md](WIRE.md#writing-to-a-bus-080).

Measured with curl against `python -m spatialdds.serve --publish --token tok
--dds-domain 177 --extension curl_claims --public spatial.owm.Entity`, a DDS
reader on another participant printing what arrived:

```console
$ curl -X PUT -H "$H" --data @bench.json $E/ent:bench
201
$ curl -X PUT -H "$H" --data @cyclist.json $E/ent:cyclist        # OBSERVED, no ?refresh=
400 urn:spatialdds-web:problem:refresh-required
$ curl -X PUT -H "$H" --data @cyclist.json "$E/ent:cyclist?refresh=5"
201
$ curl -X PUT -H "$H" --data @claim.json $C/near-1               # asserter left empty
asserter 'operator'
... 7 s, nobody refreshes the cyclist
$ curl -o /dev/null -w "%{http_code}" $E/ent:cyclist
404
$ curl -o /dev/null -w "%{http_code}" $E/ent:bench
200
```

```console
bus   3.6s entity sample  ent:bench      basis=DECLARED state=ACTIVE
bus   3.6s entity sample  ent:cyclist    basis=OBSERVED state=ACTIVE
bus   3.6s claim  sample  near-1         basis=DECLARED state=CREATED asserter='operator'
bus   8.5s entity sample  ent:cyclist    basis=OBSERVED state=RETIRED reason='refresh lapsed'
bus   8.5s entity dispose ent:cyclist    reason='refresh lapsed'
```

Restarted on the same journal, five entries replayed: the bench and the claim,
and not the cyclist. `gates/entity_publishing.py` holds all of it, with the
lapse, the asserter rule and the basis rule each shown failable by a planted
fault.

## Access

**Lanes: tokens scoped to scenes.** A scene — the first segment of every
resource path, and the second of every `spatialdds://` URI this endpoint holds
— is a lane. A token resolves to a principal (a name, a provider identity, the
scenes it may write, and whether it may create scenes), and every `PUT`,
`DELETE` and command `POST` checks the path's scene, or a manifest URI's scene
segment, against the grant:

| | |
| --- | --- |
| no token, or one nobody issued | `401`, with `WWW-Authenticate` |
| a valid token, outside its lane | `403` `not-your-lane`, naming the scene and the grant |
| a scene that does not exist, from a principal that may not create scenes | `403` `scene-creation-not-granted` |

A scene exists when the endpoint serves it or a grant names it, so an operator
stands up an empty lane by naming it in a grant, without handing anyone the
right to mint scenes. The wildcard grant `*` covers every scene that exists and
creates none unless `may_create_scenes` is also set. **Reads stay open**: an
operator-scoped resource still wants a credential, and any principal's will do.

The table lives in a file, hashed — SHA-256 of 256-bit random tokens, never the
token itself — and `python -m spatialdds.tokens` issues (printing the token
once), lists and revokes rows. `python -m spatialdds.serve --tokens FILE`
serves it and re-reads it on `SIGHUP`, without dropping a connection; a file
that fails to load is refused and the old table keeps serving. `--token TOK`
still works and means what it always did: one principal, every scene.

A withdrawal is complete now: `DELETE` on the announce disposes it, and
`DELETE /spatialdds/resolve?uri=…` withdraws its manifest under the same lane
rule (finding 44). Both are journalled.

**Virtual authorities: designed, not built.** Today one endpoint answers as
one authority over every lane. The hook is `tenancy.AUTHORITIES` — an
authority name mapped to the scenes it answers for, selected by the request's
`Host` header — and nothing reads it yet. It becomes real when a partner wants
their own DNS name over their own lanes.

A record is accepted only into the authority this endpoint answers for, which
is the same `--public-url`-derived authority the resolver uses, checked against
**every** `spatialdds://` URI the record carries at any depth. Another
authority's URI is `403` naming it.

There is no state in which that check has nothing to check: a bound endpoint
always has a host, so with no `--public-url` the authority is `host:port` — a
coherent namespace, just a local one. Publishing against it works and is almost
never what was meant, so the gateway says so at startup. The empty-base guard
in `publish.check_authority` is for a programmatic caller and is unreachable
over HTTP; `gates/publish_reject.py` checks it where it lives and checks why
the HTTP path cannot reach it.

## Limits, stated and enforced

Every limit refuses with a problem document that names it and its number.

| limit | default (`serve` flag) | refusal |
| --- | --- | --- |
| request body | 256 KiB (`--max-body`) | `413 body-too-large` |
| request target, path and query | 16 KiB | `414 uri-too-long` |
| body content type | `application/json` | `415 unsupported-media-type` |
| records per scene, per principal | the token table's `max_records` | `403 quota-exceeded` |
| concurrent WebSocket subscriptions | 256 (`--max-ws`) | `503 too-many-subscriptions` |
| requests per client IP | 20/s, burst 60 (`--rate`) | `429 rate-limited`, `Retry-After` |
| the journal cannot be written (disk full) | — | `503 journal-unwritable`, `Retry-After`; reads unaffected |

Deployed behind Caddy, the TLS front's own limits sit above these (body 1 MB,
headers 32 KB), so this endpoint's documents are what a client sees. The
client IP is the rightmost `X-Forwarded-For` entry when the peer is the local
proxy, and the peer otherwise; per-IP limiting lives here rather than in Caddy
because the Caddy the deploy installs has no rate-limit directive (finding 56).
`gates/limits.py` tests each at its boundary, both sides.

## Validation at the door

Nothing is stored before a body has passed, in this order: the vendored
generated schema (the gates' own `jsonschema` checker), the authority check
above, and the wire round trip. `gates/publish_reject.py` checks that the
registry is untouched after a run of refusals — a surface that rejected a
record and kept it would be worse than one that accepted it.

## The journal is a recording

Accepted writes and disposes append to an MCAP carrying **the same embedded IDL
the seed carried, byte for byte**, so a backup is readable by any SpatialDDS
tool rather than only by this one. On startup the seed replays first, then the
journal. A gateway given only a journal learns its types from the journal
itself; one given neither serves an empty registry and says that publishing
needs types.

The journal is rewritten whole and renamed into place on each accepted write.
MCAP puts its summary at the end, so appending in place leaves a file that is
not a complete MCAP until the process exits cleanly — and a journal valid only
after a graceful shutdown is not a backup. A registry of this size makes the
rewrite cheaper than an append-and-repair scheme.

**Every entry carries its principal and its scene** — a sample as a
`spatialdds-web:written-by` metadata record beside it, keyed by sequence
number; disposes, manifests and manifest withdrawals (`spatialdds-web:
manifest-withdraw`) in their own records — and replays with them. A journal
written before lanes replays with no principal, which is the truth about it.
`python -m spatialdds.journal --journal J reset-scene S` is the per-lane reset:
stop the registry, run it, start it. It removes that scene's entries and
writes every other entry back with its own sequence number and bytes, so every
other lane serves exactly what it served before (`gates/tenancy.py` asserts
both). `list` counts entries per scene and principal.

**A dispose has no 1.8 record, and that is a gap rather than a design.**
`Depart` is `{service_id, stamp}` — a service withdrawal — and nothing
expresses a content withdrawal at all. Worse, `Depart`'s well-known topic has
four segments and so cannot be addressed as an N.3 resource (finding 16). So
the journal carries disposes as MCAP metadata records named
`spatialdds-web:dispose`, which is readable by any MCAP tool and is a
convention of this endpoint rather than of the binding. Labeled here, gated in
`gates/journal_replay.py`, and recorded as finding 25 and a gap in the
candidates entry.

## The asset route, also implementation-defined

`GET /assets/{name}` serves the bytes in `--assets` (default
`samples/assets/`), public-cacheable with an ETag over the bytes. Appendix N
defines no asset route: a manifest's `assets[].uri` is a plain URL and usually
points at a CDN, which is where this repository's committed fountain manifests
point. It exists so the publish walkthrough can be followed without a push and
without the network — a chain ending at a URL that only resolves after the
commit is published is not a chain anyone can run. Point the duck's authored
ref at an external URL and the route goes unused.

One path segment, resolved and required to land inside the assets directory.
That rule is load-bearing rather than defensive: aiohttp matches the raw
segment and decodes afterwards, so `..%2F..%2Fx` arrives as a name with
slashes in it. `gw_http.asset_path` is a separate function so the rule can be
checked over names a request cannot carry, and `gates/publish_duck.py` checks
it both ways.

## Live behaviour

A publish accepted while subscribers are connected streams as a `sample` on the
topic's WebSocket; a dispose streams as a `dispose`. Same envelope a replayed
sample has — a client that had to distinguish them would be reading a different
protocol depending on where the data came from. The search index reflects a new
announce without restart.

This is the one read-side behaviour the flag changes: with it on, a subscription
stays open after the recorded events instead of closing. With it off, the socket
terminates exactly as before, which every existing WebSocket gate depends on and
`gates/publish_offswitch.py` asserts.

## Deferred, and recorded rather than silently absent

- **Leases and expiry for records.** The world lanes have them since 0.8.0
  (`?refresh=`, above). The record lanes do not: `Announce` and
  `ContentAnnounce` carry `ttl_sec` and nothing enforces it yet.
- **Per-record ownership.** Tokens are scoped to scenes (above); within a
  scene every principal granted it may replace or withdraw every record in it.
  A lane is the unit of ownership, not a record.
- **Server-set identity fields.** A claim's `asserter` comes from the
  credential since 0.8.0 (above). The records' `provider_id` and `org` are
  still read from the request body. They should come from the credential — the OSCP census's
  platform-set SCR fields are the precedent — and the design rule that outlives
  this batch is that identity never comes from the body. This batch does not
  honour it, which is why the rule is written into the candidates entry rather
  than only into this file.
- **Upstream WebSocket streaming** for high-rate publishers.

## Gates

`golden_publish`, `publish_reject`, `journal_replay`, `publish_live`,
`publish_offswitch`, `publish_duck`, `publish_anchor`, `tenancy`, `limits`,
`fuzz`, `security`, `entity_publishing`; and `tools/torture/` for disk-full, torn writes and the
soak, which need a container rather than a gate's minutes. The walkthrough in
[WALKTHROUGH.md](WALKTHROUGH.md) is walked command by command by
`gates/quickstart.py`, including its step 8, which prints the three published
documents in full -- the VPS manifest, the duck's manifest, and the announce
from its own instance URL -- so the record a client receives is on the page
rather than described. Expected values come from
`samples/published-records.input.json`, which is authored data; see
[EVIDENCE.md](EVIDENCE.md).
