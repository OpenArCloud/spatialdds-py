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

## The verbs

| | |
| --- | --- |
| `PUT /{domain}/{stream}/{type}/{version}/{key}` | publish an instance: `201` created, `200` replaced |
| `DELETE /{domain}/{stream}/{type}/{version}/{key}` | dispose it: `204`, and the dispose streams on the topic's WS |
| `PUT /spatialdds/resolve` | publish a content manifest, keyed by the `id` it declares |

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

Exactly two record kinds are publishable: `announce` and `content_announce`,
the SSD and SCD successors. Everything else this endpoint serves stays
read-only, and a `PUT` to any other topic is a `405` naming the two. Both 1.8
types require every member, so a body is a whole record and never a patch;
there is no `PATCH`.

## Access

Bearer token, the endpoint's existing operator credential — no second kind of
credential is minted. No token or a wrong one is `401`.

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

- **Leases and expiry.** A registry must eventually shed dead publishers, and
  SSD parity requires it. Both records carry `ttl_sec` and nothing enforces it.
- **Per-provider tokens and scopes.** One operator credential publishes
  anything; there is no notion of a publisher owning its own records.
- **Server-set identity fields.** `provider_id` and `org` are read from the
  request body. They should come from the credential — the OSCP census's
  platform-set SCR fields are the precedent — and the design rule that outlives
  this batch is that identity never comes from the body. This batch does not
  honour it, which is why the rule is written into the candidates entry rather
  than only into this file.
- **Upstream WebSocket streaming** for high-rate publishers.

## Gates

`golden_publish`, `publish_reject`, `journal_replay`, `publish_live`,
`publish_offswitch`, `publish_duck`. The walkthrough in
[WALKTHROUGH.md](WALKTHROUGH.md) is walked command by command by
`gates/quickstart.py`, including its step 8, which prints the three published
documents in full -- the VPS manifest, the duck's manifest, and the announce
from its own instance URL -- so the record a client receives is on the page
rather than described. Expected values come from
`samples/published-records.input.json`, which is authored data; see
[EVIDENCE.md](EVIDENCE.md).
