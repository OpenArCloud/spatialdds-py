# Web binding, findings from the second implementation

What building the standalone gateway (web-binding step 5) found in the pinned
surface. Each is written as the smallest change that would close it, because
that is the form a specification can act on, and each says whether it blocked
anything or merely needs a sentence.

The gateway implements the surface as pinned. Nothing below was worked around
by deviating from it.

## The two that threaten the backend-blind invariant

These matter more than the rest, because the suite's one stated invariant is
that it "cannot tell whether a DDS bus or a web-native implementation is behind
it" and that "the same suite, byte-for-byte, must pass against both." Two
normative rules currently cannot hold for the web-native side.

### 1. `service.connection` is required of clients but cannot exist here

§3.3.0 says, normatively: "Clients MUST be able to extract `service.connection`
from any result and use it to join the service's DDS domain."

A standalone web-binding endpoint has no DDS domain. There is no domain id, no
peer locator and no partition, and inventing one would be worse than omitting
the block: a client would act on it and fail to connect to something that does
not exist. §8.2.3 makes `service.connection` OPTIONAL, so this gateway omits it
and its search response is schema-legal. But a conformance check written to the
§3.3.0 sentence would require the block, and the standalone shape could never
pass the suite that exists to certify it.

*Smallest change:* make the rule conditional on the block's presence — "where a
result carries `service.connection`, clients MUST be able to use it to join the
service's DDS domain" — and add one sentence that a service with no DDS domain
behind it omits the block.

### 2. `bootstrap` and `resolver` have no truthful content here either

Both well-known paths exist to hand a client the parameters for reaching a DDS
domain. The pinned surface lists them as "existing; unchanged", which reads as
required. This gateway returns 404 with a body saying why, because 404 is what
§3.3.0 already defines for a well-known path an authority does not support, and
a bootstrap manifest with an invented `domain_id` would be a lie a client acts
on.

*Smallest change:* state that these two are required only of an endpoint
fronting a DDS domain, and that a standalone endpoint returns 404, so a
conformance check accepts either a valid manifest or a 404 rather than
requiring presence.

## The envelope

### 3. There is no way to say why a subscription ended

The four event names are fixed: `sample`, `end_of_replay`, `dispose`,
`liveliness`. None of them can carry "your subscription is ending, and here is
why." The only remaining channel is closing the socket, and the same section
says a client "MUST NOT infer liveliness or removal from the WS connection
dropping" — correctly, which is exactly what leaves a server with nothing to
say when it has to stop.

So a client cannot distinguish an authorization that expired, a topic that was
withdrawn, and a server restarting. All three arrive as silence.

*Smallest change:* either a fifth event name, or a sentence pinning the RFC 6455
close code and requiring the reason phrase to carry a machine-readable token.
The second is cheaper and needs no new event.

### 4. A replayed set does not say which instant it is

`end_of_replay` carries `topic` and `replayed` and, normatively, "no other
fields." That is the right discipline for a count, but it means a client holding
a latched set cannot tell what instant the set belongs to.

For a live bus the answer is "now" and the question does not arise. For this
gateway it does, visibly: a GET returns the set as of the end of the recorded
window, because the gateway's knowledge is complete, while a subscriber replays
the set as of the window's start and then watches the window play out. Both are
correct and a client cannot tell them apart from the wire.

*Smallest change:* an optional `as_of` timestamp on `end_of_replay`, with the
existing "no other fields" rule relaxed to name it.

### 5. `liveliness` is per key, and a translation sidecar has no per-key source

The envelope makes liveliness per instance, which matches DDS. But SceneScape's
northbound MQTT has no writer-liveliness concept at all, so there is no upstream
fact to translate. This gateway is itself the writer, so it reports the only
liveliness it holds: it emits `alive: false` per key when it stops serving a
topic, rather than letting the client infer the end from the socket closing.

That satisfies the letter of the rule and is honestly weaker than DDS
liveliness. A conformance suite can observe the event; it cannot observe that
the event reflects an upstream producer, and against this endpoint it never
will.

*Smallest change:* say what liveliness means for an endpoint that is itself the
writer, and that an endpoint with no upstream liveliness source reports its
own.

## Mapping and documentation

### 6. Two kinds of `stamp` arrive from one endpoint

`MAPPING.md` makes `int64` a decimal string unconditionally, so a
`builtin.Time` on a resource is `{"sec": "1791004157", "nanosec": 281000000}`.
§8.1 defines a manifest's `stamp` as `{"sec": <int>, "nanosec": <int>}`, a JSON
number, and manifests are JSON-native documents rather than IDL types.

Both come out of this one endpoint: the search path serves manifests, the
resource paths serve mapped IDL. A client written to "parse `stamp.sec`" sees a
string on one and a number on the other. Nothing is ambiguous once you know
manifests are outside the IDL mapping; the binding text does not currently say
so.

*Smallest change:* one sentence in the binding stating that manifest documents
are JSON-native and not subject to the canonical IDL mapping.

### 7. `golden/README.md` still describes the superseded presence mapping

Documentation only, and it cost us a few minutes. That file says
`expected_json` has "guards dropped, guarded members present/absent
accordingly", and describes `frameref_guards.json` as "`has_*` guard →
presence (one guard false → member absent…)".

`MAPPING.md` (brief v1.2) reversed that to the literal rule, and the vector
itself, its `exercises` list and the generated schemas are all correctly
literal. Only the prose around them is stale.

*Smallest change:* two sentences in `web-binding/golden/README.md`.

## What this gateway invented, flagged as required

The pinned surface says never a local deviation and never an invented endpoint.
No endpoint was invented. Three smaller things were, all additive, and all
declared here rather than left to be discovered:

1. **`url` and `access` on the service manifest's topics entries.** §8.2.3 says
   `service.topics` is an array of "`TopicMeta`-shaped objects", and
   `TopicMeta` has no URL. Without one, a client that has a manifest still
   cannot reach a resource: it would have to know the latched-resource path
   mapping and rebuild each URL by string surgery on the topic name. Adding the
   resource's own URL is what makes the ten-line client ten lines — it reads
   `topic["url"]` rather than reconstructing it. `access` names the resource's
   access class so a client knows which topics need a token before it is
   refused. *Candidate for ratification: a `url` field on the manifest's topic
   entries is the single highest-value addition found here.*

2. **`X-SpatialDDS-Topic`, `-Type` and `-Access` response headers.** The
   on-bus topic name, the type FQN and the access class of the resource just
   served. **Non-normative**, by ruling: diagnostic only, nothing requires
   them, no gate depends on them, and no conformance check should.

3. **The shape of error bodies.** 401, 404 and 400 bodies are JSON objects with
   an `error` key. The pinned surface fixes status codes, not bodies.
   *Resolved at step 7.* The evaluation came out for adoption: RFC 7807's
   members map cleanly onto what these bodies already carried, so Appendix N.8
   adopts `application/problem+json` and deprecates the ad-hoc shape. This
   gateway migrated with it — `type`, `title`, `status`, `detail`, with the old
   context kept as RFC 7807 extension members, and a local gate asserting the
   shape so it is not merely believed. Nothing was invented at the
   specification level: RFC 7807 problem types belong to the service that
   raises them, so the `urn:spatialdds-web:problem:` namespace is this
   gateway's own.

## Out of scope, stated rather than stubbed

* **The command POST surface.** The conformance definition has a
  "Commands = POST" check with Appendix M.1 decline semantics. This sidecar is
  northbound only: it translates SceneScape into SpatialDDS and never writes
  back. It has no command lane, so there is no verb to accept and no decline to
  return. Nothing here simulates one, for the same reason the `spatial.owm`
  lane publishes no `ModelCommand`: a fabricated decline would make the
  coverage table read as exercised when nothing was exercised.
* **Southbound anything.** Unchanged from every earlier phase.
* **SceneScape touchpoints.** Still zero. Rollback is still "stop the
  gateway", and now also "stop the sidecar", which are two separate processes.

## Dispositions, 2026-10-07

Ruled on the report of this build, recorded here because this file is what
travels to the step-7 queue and a ruling that is not written down is a ruling
that has to be made twice.

**The two backend-blind threats (1 and 2) are closed by one principle.** The
conformance suite treats all DDS-join material as optional-and-validated, never
required: `service.connection`, `bootstrap` and `resolver` are correct when
present and conformant when absent, as a 404 or an omission. The suite stays
blind, because it never infers which shape it is talking to; it simply does not
demand DDS of an endpoint that never claimed any. §3.3.0's MUST sentence gets
rescoped at step 7 to deployments that offer DDS access, and finding 1 is the
argument on file for that edit.

**3, subscription end.** A terminal event carrying a reason. The contract
forbids reading the close, so the close cannot be the message. Same principle
as removal, where a dispose is an event rather than an inference.

**4, `end_of_replay` as-of.** Accepted as a surface amendment candidate: the
marker gains an as-of stamp.

**5, liveliness granularity.** Becomes something an endpoint declares: per-key
where the source supports it, per-writer where it does not. The honest-subset
principle applied to liveliness rather than a single granularity imposed on
sources that cannot produce it.

**6, manifest `stamp.sec`.** Reclassified from an ambiguity to a coherence bug,
and the fix goes the other way from what this file proposed. A manifest is JSON
produced by this binding, so it follows the canonical mapping like everything
else, decimal string included. Not a sentence exempting manifests; a correction
to the manifest.

**7, `golden/README.md`.** Documentation fix, spec repo side.

**Inventions.** `url` and `access` on manifest topic entries are ratified and
enter the pinned surface as normative at step 7: the `url` turns discovery into
hypermedia, so a client follows links instead of constructing paths, and that
is the correct web shape rather than a convenience. The diagnostic headers are
accepted as non-normative. The error bodies are provisional pending the RFC
7807 evaluation above.

**Northbound only** remains the honest census line. Nothing simulates a command
lane.

## Findings from the standalone rebuild, 2026-10-07

Found while rebuilding the gateway on a recording instead of on an adapter's
router. The data source is now a typed SpatialDDS MCAP read through the IDL it
embeds, which put the published artifacts under a kind of pressure the first
implementation never applied: it had generated bindings to hand and never had
to read the wire from what a recording actually carries.

### 8. The published serializer tables cannot drive a decoder

`serializer_tables.json` describes `double[3]` and `sequence<string,32>`
identically, as `{"t":"array"}`. For emitting JSON that is correct, because
both become JSON arrays. For reading CDR it is fatal: a sequence is
length-prefixed and a fixed array is not, so the distinction the tables erase
is exactly the one the wire needs.

Anything that reads SpatialDDS off a bus or out of a recording and serves the
binding therefore needs more than the published tables. This repository parses
the IDL for layout and uses the tables as the cross-check.

*Smallest change:* record the collection's nature in the table entry — a bound
for a fixed array, its absence for a sequence — which the generator already
knows when it walks the AST.

### 9. The reference compiler cannot read a recording's embedded schema

An MCAP schema record written by a SpatialDDS producer says, in its own
preamble, that it is "a single self-contained translation unit". `idlc -l py`
compiles the specification's `events.idl` without complaint and **segfaults**
on the flattened schema for the same types. Reproduced on each of the three
distinct schema payloads in the sample recording, with comments stripped and
with comments intact; the `types.idl` section alone compiles, so it is not the
flattening format as such.

The consequence is concrete: a consumer cannot rebuild types from a recording
using the reference toolchain, which is the main thing self-description is for.

*Smallest change:* upstream bug report against the compiler. Drafted but
**not filed**, pending James's word, on the same footing as the Foxglove
deserializer issue: nothing goes to another project's tracker without it.
Separately, worth deciding whether the flattened payload is meant to be
compilable at all, and saying so in the recording guidance either way.

### 10. Eleven published schemas carry Python-mangled member names

`MAPPING.md` requires struct members under their **verbatim IDL names**. The
IDL declares `boolean global;` and `Time from;`. Python reserves both, so
`idlc` renames them `_global` and `_from`, the generator reflects over its
generated Python classes, and the renames are published: `_global` and `_from`
appear 18 times each across 11 schemas and 4 serializer tables, including
`spatial.disco.CoverageElement`, `CoverageQuery`, `CoverageResponse`,
`Announce` and `ServiceSummary`.

So the discovery types are affected, and the specification contradicts itself:
§3.3.4's coverage examples and §8.2.3's manifest example both write
`"global": false`, while the generated schema for the same concept requires
`_global`. Worse, it fails silently — `additionalProperties` is true, so a
conforming producer's `global` is ignored as unknown and `required` reports
`_global` missing.

*Characterised:* `gen.py` reflects over the Python classes `idlc` produced and
reads their member names, so it reads `idlc`'s renames rather than the IDL's own
identifiers. The AST carries the original names; the generator is looking in the
wrong place.

*Smallest change, spec-side:* emit verbatim names from the AST's original
identifiers, regenerate all 112 schemas, and **add a generator gate asserting
that no schema property name begins with an underscore unless the IDL member
does.** That assertion is not belt-and-braces. `additionalProperties: true`
makes the mismatch silent in both directions — a conforming producer's `global`
is ignored as unknown, and `_global` is reported missing by `required` — so the
tolerant reader cannot be the detector. Something has to assert it.

### 11. A recording carries types but not QoS

Nothing in an MCAP says a topic was TRANSIENT_LOCAL. The binding's entire shape
rests on that split — latched state is a GET resource, a stream is a WebSocket
— so a gateway handed an unfamiliar recording cannot tell which resources are
which. This repository declares durability per type from the specification's
QoS table, which works for known types and fails for a profile it has not seen.

Related, and the same cause: "the latched set as of the start of the window" is
not stated either. It is inferred here from log times, an instance whose first
sample shares the earliest log time on its topic being taken as in force when
the window opened. That inference is documented where it is made.

*Smallest change:* a durability hint in the MCAP channel metadata, beside the
`spatialdds_type` and `spatialdds_version` already written there. One string
per channel would make a recording sufficient.

### 12. The empty-union-branch rule is not derivable from the IDL

`MAPPING.md` says an empty branch is `{"type":"COV_NONE"}` with no member. The
IDL declares `case COV_NONE: uint8 none;` — a real byte, present on the wire
and correctly consumed by the decoder. That the placeholder is padding rather
than information is a mapping decision recorded only in the generated tables,
where the branch's type is `null`.

A second implementation reading the IDL alone has no rule that recovers it.
This one consults the tables for union shape, and only for that.

*Smallest change:* an annotation on the placeholder member, or one sentence in
MAPPING.md naming the convention a generator applies, so the rule is in the
contract rather than only in its output.

## A defect class, named because it has now happened twice

### An instrument that cannot report what it claims to measure

Two in two days, in work that was otherwise careful, and they are the same
thing:

* A check written `joined_would_differ == 0 or True`, as a hedge while the
  number was unknown. It could not fail. It sat in a gate reporting `ok` on
  every run.
* A validation loop that collected decode errors into a list capped at five
  entries and then reported `len(errs)` as the error count. It printed
  "decode errors: 5" when the true figure was in the thousands, and the
  accompanying "JSON differences: none" was true only of the handful that had
  decoded. The result was reported as near-perfect. It was near-meaningless.

Neither was caught by review. The first was caught by asking what the number
actually was; the second by a figure that did not fit — a two-camera scene
advertising four camera topics — several steps later.

What they share is that the instrument was never tested. A gate's assertions
get fault-injected as a matter of course in this work; the *counters and
summaries that report on those assertions* were not treated as needing the same
treatment, and both defects lived there rather than in the logic being checked.

**The rule, stated so it can be applied: a check or a counter is
fault-injected, or it is decorative.** Before trusting a number, make the thing
it measures go wrong and confirm the number moves. A cap, a truncation, a
`[:5]`, a `break`, a default of zero, an `or True` — each is a place where an
instrument stops measuring and starts reassuring.

The honest figure for this build, from an instrument that was made real first:
15,676 of 15,676 messages decoded and projected byte-identical against a
CycloneDDS reader.

### A readiness probe that cannot tell "mine" from "something"

The same class, in a different costume. The gateway was started on a fixed port
and checked with a loop that fetched a URL until it answered. It answered
immediately, every time, because a gateway from an hour earlier still held the
port: every later start died with `EADDRINUSE` and every verification
afterwards ran against code from before the fixes it was meant to verify.

The probe was not wrong about the server being up. It could not distinguish
*the server I started* from *a server*. `tools/run_gateway.py` now refuses a
bound port rather than failing softly, and the readiness check confirms the
listening process is the one it started. `gates/harness.py` avoids the question
entirely: bind port 0 and be handed the port once the socket is listening, so
there is nothing to poll.

## Dispositions, findings 8 to 12, 2026-10-07

**8, the serializer tables cannot drive a decoder.** A generator defect in the
specification repository, not a local workaround. Travels to the spec agent
with the evidence above. The decoding path it forces is ratified and documented
in this repository's README as normative: parse the IDL for wire layout, use
the tables as the cross-check.

**9, `idlc` segfaults on a flattened schema payload.** Possible upstream
report, drafted and held pending James's word, like the Foxglove deserializer
issue.

**10, Python-mangled member names in eleven published schemas.** The headline.
Characterised as a generator bug: `gen.py` reads names off the classes `idlc`
emitted rather than off the AST's original identifiers. Fixed spec-side, all
112 schemas regenerated, plus the underscore assertion, because
`additionalProperties: true` means the tolerant reader cannot be the detector.

**11, a recording carries types but not QoS.** Adopted with the one-string
fix: the SceneScape adapter's MCAP writer adds durability to channel metadata
on its next touch, beside the `spatialdds_type` and `spatialdds_version` it
already writes. Queued in that repository's lane, not urgent; declaring
durability from the specification's QoS table is correct in the interim, which
is what this gateway does.

**12, the empty-union-branch rule is not derivable from the IDL.** Stands as
reported. This gateway consults the tables for union shape and only for that,
documented where it happens.

## Closed by the spec-side batch and this one, 2026-10-07

**8 and 10 are fixed upstream.** `gen.py` now reverses `idlc`'s Python-keyword
renames, so the eleven schemas carry `global` and `from` verbatim; and a
serializer table distinguishes a fixed array from a bounded sequence, `length`
against `bound`, including for octet payloads where base64 had hidden the
difference completely. Both carry a new generator gate, `gate_names.py`
asserting that no published property name begins with an underscore unless an
IDL member does. This repository re-vendored `webjson/` at the fixed head and
repinned every digest; `gates/wire_model.py` no longer reconciles the two
spellings but **fails** on the mangled one, across all 161 vendored table
entries rather than only the ones this recording's IDL also declares — the
intersection could not see the discovery types, which is exactly where the
defect had been.

**A third, found by one review question and fixed by removal.** The first draft
of Appendix N.7 required `qos_profile` to carry `TRANSIENT_LOCAL` or `LIVE`.
`LIVE` appeared nowhere else in the specification, and `qos_profile` is already
a free-form profile *name* in §3.3.2 (`VIDEO_LIVE`, `POSE_RT`), so durability
carried there collided with an established use. Final N.7 gives durability its
own member taking the DDS kinds verbatim, `"VOLATILE"` or `"TRANSIENT_LOCAL"`,
inventing no vocabulary at all. This gateway emits it and omits `qos_profile`,
having no deployment profile name to put there.

**11 remains open**, in the adapter's lane: a recording still carries types but
not QoS, so durability is declared here from the specification's QoS table
rather than read from the file.

## Findings from the anchor-bearing sample, 2026-10-09

Three, from authoring `samples/fountain-anchors.mcap` and running the suite
against a gateway serving it. The fourth and largest — that no 1.8 anchor type
can reference content — is written up as entry 2 of
`directions/spatialdds-1.9-candidates.md`, since it needs a wire change.

### 13. Appendix N.3 states no encoding rule for an instance key

The instance path is `{base}/{scene}/{stream}/{type}/{version}/{key}`, and the
key is the value of the `@key` member. Nothing says how a key containing the
path separator is carried — and `GeoAnchor.anchor_id` is documented in the IDL
as `e.g., "anchor/4th-and-main"`, so **the specification's own example contains
a slash.**

Unencoded, such a key is simply unroutable: the path gains a segment and the
request 404s with nothing to indicate why. Percent-encoded as one segment it
works, and that is what this gateway serves and what
`gates/anchor_serve.py` requests. But a client holding a collection and
building instance URLs from the keys in it has nothing telling it to encode,
and the manifest advertises only the collection URL, so the client must
construct the instance path itself.

*Smallest change:* one sentence in N.3 — the key occupies exactly one path
segment and MUST be percent-encoded, with `/` as the case that matters. An
erratum to published text rather than a 1.9 feature.

### 14. Two conformance checks fail an endpoint for being homogeneous

Running the suite against the fountain recording, whose resources are all
public and all latched:

```
FAIL  access classes, both directions: both classes must be present to test
      both directions (2 public, 0 gated)
FAIL  the carried QoS subset is observable: every topic declares the same
      durability ({'TRANSIENT_LOCAL'}), so the distinction is not carried
```

Both endpoints are conformant. An anchor recording is site configuration and
discovery data throughout — it contains no observations of people, so nothing
in it *should* be operator-scoped — and every resource in it is latched state,
so nothing *should* be volatile. The checks are reporting an absence of
counter-examples as a defect.

These are both my own non-triviality guards, added so that a check could not
pass by having nothing to look at. The guard is right; the verdict is wrong.
**Zero observations is not evidence — and an absence of counter-examples is not
a defect.** That second half is the mirror of the principle already stated in
`conformance/README.md` and I did not draw it.

*Smallest change:* each check asserts everything the endpoint's data can
exercise — that public resources are served uncredentialed and cacheable, that
latched topics replay — and reports the untested direction as **UNEXERCISED**
rather than FAIL. The vocabulary already exists for exactly this.

### 15. §8.2's skip reason predates the finding that now explains it

§8.2 still skips with "the open-anchors option-2 acceptance criteria land in
`directions/` before this check is built". True when written, and no longer the
operative reason: the criteria are not what blocks it. The scenario reads
"fetch **an anchor's asset by reference**", and no 1.8 anchor type can carry
that reference, so no recording can un-skip it.

*Smallest change:* restate the skip reason to cite the missing
`GeoAnchor.manifest_uri`, so the manifest says what is actually in the way. The
scenario text itself is **not** reworded to something passable; it waits for
the field it was written for.

All three are spec-side, and the sample directive held the specification
read-only, so none is changed here.

### 16. The well-known discovery topics have no Appendix N path

Appendix N.3 maps a resource path one-to-one onto the §3.3.1 topic grammar,
`spatialdds/<domain>/<stream>/<type>/<version>` — five segments. §3.3's
well-known discovery topics have four:

```
spatialdds/discovery/announce/v1                4 segments
spatialdds/discovery/depart/v1                  4 segments
spatialdds/<domain>/<stream>/<type>/<version>   5 segments
```

So `Announce` and `Depart` on their well-known topics cannot be addressed as
latched web resources at all, and for a gateway fronting a real bus the
announce lane is among the first things a web client would want. This sample
sidesteps it by scoping its announce to the domain,
`spatialdds/<domain>/discovery/announce/v1`, which is within the §3.3.1 grammar
and is served; a bridged gateway reading the well-known topic has no such
option.

*Smallest change:* N.3 states how a topic with fewer than five segments is
mapped — most simply, that a well-known discovery topic is served at
`{base}/.well-known/spatialdds/<stream>/<type>/<version>`, keeping the
well-known namespace for well-known topics.

### 17. `TopicMeta` cannot carry the durability N.7 requires

Appendix N.7 makes `durability` REQUIRED on a manifest topic entry, carrying
`"VOLATILE"` or `"TRANSIENT_LOCAL"`. A gateway fronting a bus builds that entry
from `Announce.topics`, which is `TopicMeta`:

```
TopicMeta: name, type, version, qos_profile, target_rate_hz, max_chunk_bytes
```

No durability member. `qos_profile` is a free-form profile name, which is the
very overload finding 10's flag-6 resolution removed from the manifest — so the
bridged shape has nowhere to read durability from and cannot populate a REQUIRED
field without inventing one.

This gateway does not hit it: it derives the topic list from what it serves and
knows each resource's durability from its own table. A bridged gateway would
hit it immediately, and the bridged shape is the half of certification still
unexercised.

*Smallest change:* `string durability` appended to `TopicMeta`, APPENDABLE,
taking the same two DDS kinds, so the bus type can say what the manifest must.
A 1.9 candidate rather than an erratum, since it changes the wire.

## Findings from the storefront batch, 2026-10-10

Three, all found by writing [`docs/RESOLUTION.md`](docs/RESOLUTION.md) — the
characterization of how a web-binding endpoint could serve §7.5 URI resolution.
Asking what the endpoint would have to serve meant reading §7.5.2 against N.2
line by line, which is how the first one surfaced. The full argument and the
proposed wording are in that document; these are the findings it produced.

### 18. N.2 and §7.5.2 give contradictory normative orders for the same path

N.2 groups `bootstrap` and `resolver` and says of both: "Both exist to hand a
client the parameters for joining a domain," therefore a web-native endpoint
"MUST return `404`".

True of `bootstrap`. **False of `resolver`.** §7.5.2's resolver metadata is
`{authority, https_base, cache_ttl_sec}` — an authority's URI-resolution entry
point, with no domain, no peers and no partitions in it. And §7.5.2 is
unconditional: "Each authority **MUST** expose the resolver metadata at
`https://{authority}/.well-known/spatialdds/resolver`."

So for an endpoint that is both web-native and an authority for `spatialdds://`
URIs, §7.5.2 says it MUST serve that path and N.2 says it MUST `404` it. Both
normative. No endpoint occupies that position today, which is why this has
stayed invisible.

The earlier ruling is not overturned and does not need to be: it is right, for
the reason it gives, and over-broad by exactly one path. A web-native endpoint
still has no bootstrap parameters and still must not invent any.

*Smallest change:* split the paragraph. `bootstrap` keeps the DDS-access
condition and the `404`-is-conformant ruling verbatim; `resolver` gets its own
sentence conditioned on being an authority for URIs it can resolve, per §7.5.2.
An erratum to published text — no IDL moves, no wire format changes.

### 19. §7.5.2's `ttl_sec` rule and N.4's cache classes can disagree

§7.5.2: a fetched manifest's own `ttl_sec` governs its cache lifetime. N.4: cache
behaviour follows the access class. On a resolver response serving a manifest
that carries `ttl_sec`, both apply to one `Cache-Control` header and they do not
agree — this repository's committed content manifests say `ttl_sec: 86400` while
the endpoint's public class is a flat `max-age=300`.

Nothing is wrong today: the two only meet on a resolver response and this
endpoint serves none. It is a trap laid for the first implementation that does.

*Smallest change:* one sentence in N.4 — where a served document carries its own
TTL, that TTL governs its `max-age` and the access class governs the rest of the
header. Erratum.

### 20. This gateway's resolver `404` gives a reason that is wrong for that path

Found by reading our own output against §7.5.2. `GET
/.well-known/spatialdds/resolver` returns a conformant `404` whose
`problem+json` detail reads: "no DDS domain behind it, so it has no bootstrap
parameters to publish." That is the right reason for `bootstrap` and the wrong
one for `resolver`, which was never about bootstrap parameters. The honest reason
is that this endpoint is not the authority for any `spatialdds://` URI.

The status code is correct and no client behaviour changes — §7.5.3 has clients
treat any non-200 as failure — so this is an accuracy defect in an explanation,
in a body written precisely so that a reader would not have to guess. It is
wire-visible text, and the batch that found it was told to change no gateway
behaviour except where named, so it is reported rather than fixed.

*Smallest change:* one `detail` string in `gw_http.no_dds`, split per path.

## Dispositions, findings 18 to 20, 2026-10-10

Ruled by James in the same turn that ruled resolution goes real. All three are
closed, two spec-side and one here.

**18 — N.2 and §7.5.2 contradicted each other. Closed.** The erratum landed as
SpatialDDS-spec `169fabd`, splitting the paragraph exactly as the finding
proposed: `bootstrap` keeps the DDS-access condition and the `404`-is-conformant
ruling verbatim, and `resolver` gains its own sentence conditioned on being an
authority for the `spatialdds://` URIs it can resolve. The earlier ruling was
not overturned; it was narrowed by the one path it was over-broad by.

**19 — `ttl_sec` versus the access class's `max-age`. Closed.** Same commit.
N.4 now says a public-cacheable manifest's own document TTL governs its
`max-age`, and says explicitly that this grants no lifetime to an
operator-scoped resource. Both halves are implemented and gated here.

**20 — this gateway's resolver `404` gave the wrong reason. Closed, here.** It
was reported-not-fixed because fixing it was a wire-visible text change outside
that batch's scope. Implementing §7.5.2 closed it as a consequence rather than
as a separate edit: `/.well-known/spatialdds/resolver` no longer shares the
`bootstrap` handler, so the DDS reason is served only from the path it is true
of. The new `404` from that path says what is actually the case — this endpoint
holds no manifest whose URI authority is its own — and names it
`not-an-authority` rather than `no-dds-domain`.

### Still open, and not this batch

The `OPTIONS` preflight gap stands as recorded in `docs/EVIDENCE.md`: a
script-set `If-None-Match` triggers a CORS preflight this endpoint does not
answer, so explicit conditional GETs from browser script fail cross-origin
while the browser's own cache revalidation is unaffected. It rides with the
CORS/N.4 clarification candidate, which has not landed.

## Findings from implementing resolution, 2026-10-10

Two, both spec-side, both about the conformance suite rather than the
specification text, and both created by the erratum rather than revealed by it:
`resolver` has just become a path an endpoint can be REQUIRED to serve, and the
suite was written when it was a path nobody served.

### 21. The suite accepts resolver metadata without validating it, and never resolves

`check_dds_join_optional` allows `bootstrap` and `resolver` to be `200` or
`404`, and on `200` asserts only that the body is a JSON object. That was right
when both paths were DDS-join material nothing served: present-and-shaped was
all there was to check.

N.2 as amended makes `resolver` REQUIRED of an endpoint that is an authority,
and §7.5.2 pins its minimum body — `authority`, `https_base`, `cache_ttl_sec`.
The suite checks none of the three. An endpoint serving `{}` from that path
passes today, and the run against this gateway reported `resolver=present`
having looked at nothing inside it.

Worse, the mechanism the metadata exists to advertise is wholly unexercised:
`https_base` appears nowhere in the suite, so no conformance check ever resolves
a URI. The one §7.5.2 requirement with observable consequences — `200`, the
resource body, a correct `Content-Type`, at least one integrity signal — has no
coverage at all.

*Smallest change:* on a `200` from `resolver`, assert the three members and
that `authority` is the endpoint's own host; then, when `https_base` is
advertised, one resolve round-trip asserting §7.5.2's response requirements and
§7.5.3's `404` for a URI of another authority. Zero observations is not
evidence, and this is the vocabulary already adopted for exactly this: with no
authority under test the check reports UNEXERCISED rather than passing.

### 22. The suite cannot certify an endpoint whose advertised origin differs from its connect address

Running the suite against this gateway configured as `demo.spatialdds.org` but
reached on loopback fails six checks. None of the failures is the endpoint's:
N.7's `url` fields are absolute and name the public origin, and the suite
follows them verbatim while connecting to `--endpoint`.

Two distinct faults. The suite dereferences the advertised host directly, which
needs DNS the certifying machine may not have; and it converts an advertised URL
to a path by slicing `len(ep.base)` characters off the front —

```
run.py:123   out.append({"path": url[len(ep.base):], **t})
run.py:503   msgs = _ws_collect(ep.ws_url(topic["url"][len(ep.base):]), ...)
```

— unguarded, where `run.py:153` does guard with `startswith(ep.base)`. When the
two origins differ in length the slice lands mid-string and the symptom names
nothing useful:

```
FAIL  §8.1 hello spatial in ten lines: Port could not be cast to integer
      value as '8812ds.org'
```

This is not hypothetical for certification. The hosted demo endpoint will sit
behind a TLS terminator and advertise `https://demo.spatialdds.org` while any
local or tunnelled certification run reaches it at another address — and the
bridged shape, the half of N.10 certification still unexercised, is if anything
more likely to be certified through a tunnel than less.

*Smallest change:* a second optional runner input, the advertised origin,
rewritten to the connect origin in exactly one place, and the two unguarded
slices replaced by that one function. `gates/harness.py` in this repository has
the same seam under the same constraint — `Gateway.connect` — and it is one
method because it must be the only place the substitution happens.

## A second defect class, named because it shipped, 2026-10-11

### 23. The README's quickstart failed on its first command, and every gate was green

Reported against `cc881b5` from a fresh clone. Serve per the README's own serve
line — `--mcap samples/fountain-anchors.mcap` — then run its tier-three command,
`examples/hello_spatial.py`, and get `StopIteration`. The script asked for
`spatial_zone`; the fountain recording has none. The same mismatch would have
reached production, because `demo.spatialdds.org` serves the fountain recording
and the README's tier three runs against `$BASE`.

**Why thirteen gates missed it.** `gates/examples.py` runs each example against
the recording *its own marker names*, and the marker on that tier said `retail`.
So the tier was green, the example was green, and the page was broken: the gate
honoured the annotation instead of the document. The serve line was never read
by anything.

**The class.** *A suite that checks the parts can be blind to their
composition.* It is the sibling of the self-observing check this repository has
named six times, and it fails the same way — by being locally true. Every
individual assertion held; the sequence a reader actually types was never
executed. An annotation that tells a gate what to do is a second source of
truth, and the document is the one that ships.

**What closes it,** beyond the four scripts repaired:

- `gates/quickstart.py` executes the front page top to bottom — install line,
  serve line as a subprocess, then each tier in document order against that one
  endpoint — and asserts that **every marker on the page names the recording the
  serve line serves**. That structural check is the one that prevents
  recurrence; the rest would pass again tomorrow without it.
- The mismatch sweep in `gates/examples.py` runs every script against every
  configuration it did not declare and requires a sentence rather than a
  traceback. It immediately found three more instances of the same class —
  `zones.py`, `anchors.py` and `scd_content.py` all ended in `StopIteration`
  off the front page, and `scd_content.py` additionally raised `KeyError:
  'https_base'` against a non-authority endpoint, a trap created by this
  repository's own documentation one commit earlier.
- The documented refusal is itself gated (`py-fails`), because a newcomer's
  first failure is text they read.

*Generalisation worth keeping:* where a document and an annotation can disagree
about what to run, gate the document. The annotation is for the gate's
convenience and the document is the product.

## Dispositions, findings 21 and 22, 2026-10-11

Both closed upstream by SpatialDDS-spec `c8743ef`, "Conformance suite: resolver
coverage + origin independence". Neither needed anything here.

**21 — the resolver surface was unchecked.** The suite gained
*resolver is a checked surface (§7.5.2)*. Against this gateway as an authority it
reports what it actually observed: `resolver 200 (authority
demo.spatialdds.org); 2 resolved, 1 announced-but-not-held(404); ttl 2
doc-governed / 0 class-default; error branches 400/400/404`. Against a
non-authority endpoint it reports the resolve surface UNEXERCISED instead of
passing on nothing — the vocabulary adopted for exactly this, used correctly by
someone else's code.

It also found a leg this repository cannot exercise: *no resolved manifest omits
`ttl_sec`*, so the class-default branch of the N.4 rule has no observation here.
Both committed manifests carry a TTL. Worth knowing and not worth authoring a
TTL-less manifest to fix — the branch is one `if` and the suite now says plainly
that it is untested rather than implying coverage.

**22 — the suite could not see an authority-configured endpoint.** It gained
`--connect`, which sends requests for URLs under the advertised origin to the
address the endpoint is actually reachable at, preserving the advertised `Host`
and otherwise following N.7's URLs verbatim. Documented there, as here, as a
harness substitution a client may not make. The authority shape is now
certified: 14 checks, 13 passed, 1 skipped.

That closes the loop this repository opened when it needed `Gateway.connect` for
the same reason. The seam exists on both sides now, named the same thing, for
the same stated reason.

## The instrument-defect classes, consolidated, 2026-10-11 (third added 2026-10-13)

Owed since the instances crossed six. Each class is already described where it
was found — the first at "A defect class, named because it has now happened
twice", the second at finding 23, the third at finding 29 — and a class
scattered across dated sections is not something anyone can apply. This is the
roll-up: three classes, their instances, and one sentence each.

### Class one: the self-observing instrument

An instrument whose own machinery is inside what it measures. Every instance
reported a result that was locally true and globally meaningless.

1. **The `or True` hedge.** `joined_would_differ == 0 or True`, written as a
   placeholder while the number was unknown. It could not fail, and sat in a
   gate reporting `ok` on every run.
2. **The capped counter.** Decode errors collected into a list capped at five
   entries, then `len(errs)` reported as the count: "decode errors: 5" when the
   truth was in the thousands, beside a "JSON differences: none" that was true
   only of the handful that had decoded.
3. **The §8.3 self-scan.** A conformance check that scanned sources for a list
   of banned words — including the source that contained the list. It failed
   always, for itself. Fixed by excluding its own source via
   `inspect.getsource`.
4. **The `$ref`-blind count.** The canonical-form union rule reported
   `union_tagged=0` because `$ref` was never resolved, so zero observations
   read as compliance. This is where **UNEXERCISED** came from: zero
   observations is not evidence.
5. **The patch scripts that failed their own assertions.** Two of them, because
   the comments and docstrings they had just written quoted the old strings they
   were searching for. Fixed with per-replacement assertions.
6. **The OK-with-zero-types gate.** `wire_model`'s rename reconciliation passed
   while structurally unable to see the discovery types — which is exactly
   where the defect was. Widened to all 161 vendored entries.
7. **The marker-grammar parser.** `gates/examples.py` read as input the marker
   grammar that `examples/README.md` documents by quoting it, because the
   documentation of a grammar is written in that grammar. Fixed by anchoring to
   whole-line markers, and by reporting the inline mentions it deliberately
   skips so the exclusion is visible rather than silent.

8. **The gate that repaired its input.** `gates/quickstart.py` stripped the
   `$ ` prompt and expanded `$BASE` before executing, so it certified a page
   that could not be pasted — finding 24. The variant to watch for: not an
   instrument observing its own machinery, but one *altering its subject* until
   the measurement succeeds.

The readiness probe in the earlier section is a ninth in a different costume:
it could not distinguish *the server I started* from *a server*. And the most
recent recurrence was outside a gate entirely — ad-hoc verification shell that
confirmed a listening PID, then used the port anyway after its wait expired, and
spent ten minutes talking to a gateway twenty-two minutes old. Scaffolding is
an instrument too.

**The rule, as standing: any check, counter, or patch assertion that can observe
its own machinery must exclude it or fail its injection test — and no instrument
may modify its subject in order to measure it. A gate that pre-processes the
artefact under test certifies the pre-processor.** A cap, a
truncation, a `[:5]`, a `break`, a default of zero, an `or True` — each is a
place where an instrument stops measuring and starts reassuring.

### Class two: the composition-blind instrument

An instrument whose parts are each correct and whose verdict about the whole is
wrong, because nothing executes the composition.

1. **Finding 23, the quickstart.** Thirteen gates green while the README's first
   command raised `StopIteration`. Each tier was checked against the recording
   *its own marker named*; the serve line twelve lines above was read by
   nothing. A false PASS.
2. **Finding 14, the homogeneity FAILs, as the earlier sibling.** Two
   conformance checks failed a conformant endpoint for being uniform — each
   non-triviality guard locally right, composed into a verdict about the
   endpoint that was wrong. A false FAIL.

Same blindness, opposite signs, which is why they did not look related until
both were written down. Worth recording that the remedy for one class produced
the vocabulary that fixed the other: **UNEXERCISED**, born from the `$ref`-blind
count above, is what turned finding 14's false FAILs into honest reports of an
absence.

**The rule, as standing: gate the sequence a user actually performs, and treat
the per-part verdicts as inputs to it rather than substitutes for it. Where a
document and an annotation can disagree about what to run, gate the document —
the annotation is for the gate's convenience, and the document is the product.**

### Class three: the fragment-blind instrument

The inverse of class two. An instrument that executes the composition
faithfully and never executes the pieces the way the audience will, so it
assembles context the reader does not have.

1. **Finding 29, the human path.** Ten command blocks across `README.md` and
   `docs/WALKTHROUGH.md` failed on an unset variable in a fresh shell while
   nineteen gates were green. `gates/quickstart.py` prepended each document's
   export fence before running a block — which is what a reader's shell carries
   *if they paste the fences in order from the top*, and not what anyone does.
   The gate's shell never forgot a variable, so the gate never saw it.
2. **The same shape in the examples**, found by the same walk: scripts were
   only ever run in a configuration's document order, so one that worked
   because another had run first would have passed. `publish_service.py` was
   publishing an announce whose `manifest_uri` resolved to nothing.

Related to class one's newest member rather than to class two: finding 24's
gate *repaired* its input, and this one *supplied* input the reader lacks.
Both are the instrument making the world tidier than it is.

**The rule, as standing: run the artefact the way its audience will, and where
the audience has two ways through — in order, and one piece at a time — run
both.** `gates/quickstart.py` now injects nothing, including the environment,
and `gates/examples.py` runs every script alone against its own fresh endpoint
as well as in document order.

### What all three classes have in common

The instrument was never tested against the thing it claims to measure. The fix
is the same move in all three: make the measured thing go wrong — or take away
what the instrument was quietly supplying — and confirm it says so.

And one neighbour that is none of the three, recorded here because it belongs
beside them: finding 32, a probe whose *subject* changed category while its
label did not, so it passed for a different reason than it claimed. **The three
classes are failures of sight; that one is a failure of aim** — the instrument
still pointed where the thing used to be. One instance does not make a fourth
class. If a second stale-target check turns up, this paragraph is already
standing where the class would form.

## 24. The gate repaired its input, then reported no defect, 2026-10-11

Reported by James walking the README with GitHub's copy button. Both shell
tiers failed on paste — `command not found: $`, then an empty pipe raising
`JSONDecodeError`, then the expected-output lines executed as commands — while
`gates/quickstart.py`, written one commit earlier for exactly this, passed.

**Why it passed.** The gate stripped the `$ ` prompt and expanded `$BASE`
itself before handing the command to a shell. It tested what the fence *means*.
A reader pastes what the fence *says*. The gate normalised its input into
something that worked and then reported that it worked.

**Why the fence looked like that.** `commands()` split one fence into commands
and expected output by looking for the `$ ` prefix, so the prompt marker existed
to serve the parser. The document's format was chosen for the instrument's
convenience — the inverse of the rule written down one commit earlier, and not
as an annotation this time but as the shape of the page itself. GitHub's copy
button then made that shape unrunnable, because it yields the fence verbatim and
offers no prompt-stripping.

**A new variant of the first class, and the worst-behaved so far.** The earlier
instances were instruments that could see their own machinery. This one *altered
the thing it measured until it passed*. An instrument that repairs its input is
not measuring; it is laundering.

Two more things fell out of the fix, both worth recording:

- The rewritten gate's **first run passed on zero observations** — it reported
  "0 tier(s)" and `PASS`, because no marker sat on a `sh` fence any more. Same
  catalogue, instance six, inside the repair for instance eight. It now fails
  unless every marker on the page sits on a fence holding its command alone.
- The literal walk found three further page defects the semantic gate could
  never see: the output fences showed `$BASE/...` rather than a real URL, the
  two `export BASE=` lines were stacked so that copying the wrong one gave a
  dead host and a silent `curl -s`, and nothing said the commands run from the
  repository root.

*What closes it:* command and output in separate fences, so each is copyable
alone; `gates/quickstart.py` executing the command fence byte-for-byte on the
port the serve line actually names, with no substitution anywhere and a FAIL —
naming the occupant — if that port is busy; and the environment coming from the
page's own export fence rather than from the gate.

*The rule, as standing: an instrument may not modify its subject to measure it.
If a gate pre-processes the artefact under test, what it certifies is the
pre-processor.*

## Findings from the publish path, 2026-10-13

### 25. 1.8 has no withdrawal record addressable as a web resource

Publishing needs its inverse: a record must be able to leave a registry, and a
journal of accepted writes has to be able to say that it did. 1.8 offers no way
to express it as a typed web resource.

* `Depart` is `{service_id, stamp}` — a service withdrawal, and the right
  record for exactly one of the two publishable kinds.
* Nothing expresses a **content** withdrawal. `ContentAnnounce` carries no
  lifecycle member, so the §2.14 terminal-sample-then-dispose convention this
  repository already honours for tracks has nothing to hang on.
* And `Depart`'s well-known topic is `spatialdds/discovery/depart/v1` — four
  segments, which N.3's five-segment grammar cannot address at all. That is
  finding 16, arriving from a second direction: it was a gap for reading the
  discovery topics and it is a blocker for writing them.

So a dispose cannot be journalled as a typed message. This endpoint records it
as an MCAP metadata record named `spatialdds-web:dispose`, which any MCAP tool
can read and which is a convention of this endpoint rather than of the binding.
Labeled in `docs/PUBLISH.md`, gated in `gates/journal_replay.py`, and carried
as a gap in 1.9 candidate entry 4.

*Smallest change:* either a withdrawal record that covers content as `Depart`
covers services, or an N.3 rule for addressing the four-segment well-known
discovery topics so `Depart` becomes reachable — the second is an erratum and
closes finding 16 as well, which makes it the better buy.

### 26. The publish path needed the inverse of the JSON mapping, and nobody had written it

`Writer` encodes decoded values; N.1 canonical JSON is not that. `int64` is an
unconditional decimal string in JSON and an integer on the wire, an octet run
is base64 in JSON and bytes on the wire, and a union is tagged on `"type"` in
JSON and on `_discriminator` through the decoder. So canonicalizing an accepted
body by round trip — the property that makes a published instance
indistinguishable from a replayed one — was impossible until the inverse
projection existed.

It is `webjson.Lifter`, and it is short: only those three rules actually differ
between the two representations. Everything else — enumerator names, floats,
bools, nesting — is already in the form the writer wants.

Not a specification finding. Recorded because it is the half of the mapping a
**client library** will need to send anything, and because `MAPPING.md`
documents the projection in one direction only. A second implementation writing
a publisher will have to derive this, and the three rules above are the whole
of it.

*Smallest change, spec-side:* one paragraph in `MAPPING.md` stating the three
rules as invertible, so the inverse is a reading of the document rather than a
rediscovery.

## Findings from deriving the VPS manifest, 2026-10-13

Both from adapting the specification's own `manifests/v1.8/vps_manifest.json`
into a record this endpoint can serve. Neither is this repository's to fix.

### 27. Every v1.8 manifest example carries a 1.7 version string, under the wrong key

All four files in `manifests/v1.8/` declare:

```json
"schema_version": "spatial.core/1.7"
```

Two defects in one line. The value is stale by a minor version, in the
directory named for the newer one. And the key is not the one the
specification's own prose uses: §2's profile table defines
`spatial.manifest/1.8` as the manifest profile, and §3.3.0's worked example
carries `"profile": "spatial.manifest/1.8"`. So a reader following the prose
and a reader copying the example file produce different documents.

| file | declares |
|---|---|
| `anchors_manifest.json` | `spatial.core/1.7` |
| `content_experience_manifest.json` | `spatial.core/1.7` |
| `mapping_service_manifest.json` | `spatial.core/1.7` |
| `vps_manifest.json` | `spatial.core/1.7` |

*Smallest change:* replace `schema_version` with
`"profile": "spatial.manifest/1.8"` in all four. An erratum; nothing moves but
the string the examples were already supposed to carry.

### 28. No manifest example states the URI it answers to, so none is resolvable

§7.5.2 resolves a `spatialdds://` URI to a manifest. A resolver therefore has
to know which URI a given manifest is the answer for, and nothing in
`manifests/v1.8/*.json` says: the VPS example's closest field is
`"service_id": "svc:vps:acme/sf-downtown"`, which is an identifier and not a
URI.

§3.3.0's prose example does carry it — `"id": "spatialdds://acme-vps.example/
sf-downtown/service/vps-main"` — so the envelope exists and the standalone
example files simply omit it. This repository's manifests follow the prose and
the gateway keys its resolver store on `id`, which is what let finding 18's
resolution work at all.

*Smallest change:* add the `{id, profile, rtype}` envelope to the four example
files, matching §3.3.0. Same erratum as 27, and it makes the examples usable
with §7.5.2 rather than merely illustrative. Worth a sentence in §7.5.2 too:
a manifest MUST declare the URI it resolves for, or a resolver cannot index
one it was handed.

## A third face of the same class, 2026-10-13

### 29. The gates ran compositions; a person runs fragments

James ran the publish-and-retrieve path by hand and it stumbled in ten places
the suite could not see. Reproduced before anything was changed — fresh clone,
fresh shells, every command block pasted alone, every example run with nothing
before it — and the count was: **three of three command blocks in `README.md`,
seven of seven in `docs/WALKTHROUGH.md`, all failing on an unset variable.**
The suite was green on all ten.

**Why.** `gates/quickstart.py` prepended the document's export fence to every
command block before running it. That is what a reader's shell carries *if they
paste the fences in order from the top*. It is not what a reader does: they open
a second terminal, scroll to the command they want, and paste that block. The
gate's shell never forgot a variable, so the gate never saw the defect.

**The class, in its third costume.** The self-observing instrument measures its
own machinery. The composition-blind instrument checks the parts and misses the
sequence. This is the inverse of the second: **the instrument checked a
composition and missed the fragments.** A gate that assembles context the user
will not have is as blind as one that checks parts the user will not run
separately, and both are blind in the same way — the instrument's world is
tidier than the reader's.

*The rule:* run the artefact the way its audience will, and where the audience
has two ways (in order, and one block at a time), run both.

### The ten, and what fixed each

| stumble | fix |
|---|---|
| `README.md` ×3 and `docs/WALKTHROUGH.md` ×7 fail on unset `$BASE`/`$TOK` | every command block restates the exports it needs; the export-only fences are gone, because a fence that exists to be remembered is a fence that will not be |
| `SPATIALDDS_ENDPOINT=$BASE` with `BASE` unset sets it to `""`, and `environ.get(k, default)` returns `""` — so the default never applied and the script died on an empty URL | `environ.get(k) or default` in all eight examples: unset and empty now behave the same |
| `WALKTHROUGH` step 7 piped `curl` into `wc -c` and printed `0` **with exit 0** — a silent wrong answer | the byte count is read in Python, which exits non-zero with a sentence when nothing came back. *Exit 0 is not evidence*, again |
| `publish_service.py` published an announce whose `manifest_uri` resolved to "No manifest for that URI" | it publishes the manifest too, and resolves it back; all three publish examples are now whole round trips |
| every example answered an unreachable endpoint with a `requests` traceback | one line naming the URL tried and the serve line that fixes it, in all eight |
| a 405 or 401 said what was refused but not what to do | the refusal names `--publish` and the token, and the banner prints the export lines |
| the startup banner printed a token but not the exports that use it | it prints the paste-ready lines, with the **reachable** address rather than the advertised one — pasting the advertised name would hand the reader a variable that fails on first use |
| `hello_spatial.py` and `anchors.py` traceback against the authority-configured endpoint the publish documents tell you to serve | the banner says so when the two differ, and the unreachable sentence covers the rest |
| `README.md` listed neither `publish_duck.py` nor the walkthrough | both added |

**Gated, so none of it comes back.** `gates/quickstart.py` injects nothing,
including the environment: a block that needs an export must carry it.
`gates/examples.py` gained an isolation mode — every script run alone against
its own fresh endpoint, in addition to the document-order walks. Both were
fault-injected: a block stripped of its export, and an example made to depend
on another having run.

## Dispositions from the 1.8 revision, 2026-10-15

The revision window opened and closed with two commits, both of which this
repository had asked for. Status changes only; the findings stay where they
were written.

**Finding 17 — `TopicMeta` could not carry the durability N.7 requires.
CLOSED** by spec `9c3e1f2`. `TopicMeta` gained `string durability` taking the
two DDS kinds, APPENDABLE and wire-compatible. It was the bridged shape's first
concrete blocker, found before anyone had built one, and it is now a member a
bridged gateway can read instead of a fact it would have had to invent.

The closure creates its own small risk, so it came with a check: `durability`
now has **two** sources for the same topic — the recording's `Announce` and the
manifest this gateway derives from what it serves. `gates/anchor_serve.py`
compares them per topic. Two places that can disagree about one fact is a class
this repository has met more than once.

**Finding 15 — §8.2's skip reason predated the finding that explained it.
CLOSED, and the scenario now runs.** The restated reason was that `GeoAnchor`
carried no `manifest_uri`, so no recording could un-skip it. Spec `297c433`
added the member; the fountain recording's plaque anchor names the label's
content manifest, and §8.2 resolves it. The scenario was never reworded to
something passable — it waited for the field it was written for, and got it.

**1.9 candidates entries 2 and 3** are marked LANDED in this repository's copy
of the candidates file, pointing at the specification CHANGELOG, matching what
the spec side already did.

### 30. An older recording cannot agree with regenerated artifacts, and that is compatibility rather than disagreement

`gates/wire_model.py` cross-checks the IDL a recording carries against the
published serializer tables, and the retail recording is a **vendored
pre-revision artifact**: it was cut by the SceneScape adapter before the 1.8
revision and cannot be re-cut here. Its `GeoAnchor` has eight members; the
regenerated tables have nine.

On the wire that is exactly what APPENDABLE means — a reader tolerates a
producer that omits trailing members — so the gate now tolerates a **strict
prefix**: members appended after the recording was cut are compatibility, and
the count and the member names are printed on every run so that "the readings
agree" can never quietly mean "they differ in a way I allowed". A reorder, a
rename, or an insertion anywhere but the end still fails, which is
fault-injected.

The same fact bit `gates/golden_json.py` differently: its model came from the
retail recording alone, so the revision's two new golden vectors projected
`manifest_uri` as absent. The golden vectors are the specification's own
artifacts and span the whole type tree, and no single recording here carries
every type, so the model is now the union of both recordings with the newer
definitions winning.

This is the same question `webjson/MAPPING.md` raised in its new **Open
questions** section, arriving from a second direction: the literal rule makes
every declared member `required`, so a regenerated schema rejects a producer
that predates the revision even though the wire would accept it. This gateway
hit that too — one revision behind, it failed `validates against its published
schema` on the one member it had not regenerated to carry. The mapping records
it as a 1.9-scale decision; recorded here as the gate-level consequence, which
is that a suite comparing old recordings to new artifacts needs an explicit,
narrow, counted tolerance rather than either a failure or a shrug.

*Not a spec finding.* The specification's position is stated and consistent;
this is what honouring it costs an implementation that keeps older recordings,
and the cost is one prefix check and one model union.

## 31. `GeoAnchor.checksum` is an integrity field nothing can verify, 2026-10-15

Found by authoring a `GeoAnchor` outside a recording for the first time, which
is what publishing one requires.

`checksum` is REQUIRED on the wire and its IDL comment reads
`// integrity/versioning`. §3's manifest table calls it "Integrity hash for the
anchor data". Nothing anywhere says **what it digests or in what form** — not
the IDL, not Appendix C's binding paragraph for the 1.8 revision, not the
conformance suite. The vendored schema types it `string`.

So a receiving endpoint cannot verify it. This one accepts any string, because
there is no rule to check against, and two publishers computing "the checksum
of the anchor data" will not agree: over the canonical JSON or the XCDR2 bytes,
including the checksum member or excluding it, before or after the 1.8
revision's two appended members.

It has teeth because §7.5.2 counts "a checksum field in the body" as one of the
three acceptable integrity signals, and the conformance suite's
`_has_integrity` honours that. An endpoint can therefore satisfy an integrity
requirement with a string that integrity-checks nothing.

This repository's convention, stated where it is used and not offered as a
rule: `tools/author_anchors.py` and `samples/duck.input.json` both set it to
`sha256:` over the anchor's own *authoring entry*, which is reproducible from
committed data and is explicitly not a reading of the specification.

*Smallest change:* one sentence saying what the digest covers — the canonical
form of the record with `checksum` itself omitted is the obvious candidate,
since any other choice is self-referential — or, if it is meant to be opaque
publisher bookkeeping, say that and drop it from §7.5.2's list of integrity
signals. Either resolves it; the present state lets it count as integrity
while meaning whatever the publisher likes.

### The gap, now demonstrated rather than described, 2026-10-16

0.2.0 made this concrete without meaning to. The repository now contains **two
checksum conventions, both correct, mutually incompatible**, on records a
single endpoint serves side by side:

* **Recorded anchors** carry `tools/author_anchors.py`'s digest of the anchor's
  own *authoring entry* — the input dict the recording was written from.
  Reproducible from committed data, and reproducible only from it: a reader who
  has the served record and nothing else cannot recompute it, because the
  authoring entry is not what was served.
* **Published anchors** carry `spatialdds.records.geo_anchor`'s digest of the
  *served canonical form with `checksum` emptied*. A reader recomputes it from
  exactly what arrived, with the binding's own canonical JSON and nothing else.

Both satisfy §7.5.2's "a checksum field in the body". Neither can verify the
other's. The latched set at `/ut-austin-south-mall/frames/geo_anchor/v1` on a
publishing endpoint holds five of the first kind and however many of the second
have been published, and nothing in the record says which rule applies — so a
reader who checks integrity at all must either know the provenance of each
anchor or check nothing. That is the gap, in a form somebody can run.

**The proposal, when this reaches the spec agent: the second convention.**
`sha256` over the record's canonical form (N.1) with `checksum` set to the
empty string. It is recomputable by any reader from what was served, needs no
out-of-band artifact, is already gated here
(`gates/builders.py`: *the anchor's checksum recomputes from the served record
through webjson.digest*), and makes the field mean what §7.5.2 already counts
it as meaning. The authoring tool's digest is then the one to change, not the
specification's silence to work around — and this repository will change it,
once the rule exists to change it to.

## 32. A probe whose target changed category under it, 2026-10-15

`gates/publish_reject.py` carried a check labelled *a topic that is not
publishable*, expecting `405`. Its probe aimed at `geo_anchor` — which stopped
being unpublishable in the very batch that added anchors to the write surface.
It kept passing. It was returning `400` for a malformed body where the label
promised `405` for a forbidden topic: **the right failure for the wrong reason,
a different claim passing under the same label.** It now aims at a zone, which
has no write surface and is not about to get one.

Caught by re-reading the gate against the batch rather than trusting its green.
Nothing in the suite could have caught it, because from the outside it looked
exactly like a check that still worked.

Kin to the three instrument classes above and not an instance of any: the
instrument was not observing its own machinery, nor blind to a composition or a
fragment. Its *subject* moved, and its label did not. **Green is not evidence
that a check still measures what its label says.** When a batch changes what a
category contains, every check naming that category has to be re-read, and what
is checked is *why* it passes — not that it does.

## 33. A mapping rule written for a spelling the specification does not use, 2026-10-15

Appendix N.1 makes an octet run base64. `webjson.Projector` and `webjson.Lifter`
both implemented it, and both tested the resolved IDL type against `"octet"`.
The specification's IDL spells every such field `sequence<uint8, N>` and never
once spells it `octet`, so the condition **never matched and the rule never
ran**: a `sequence<uint8>` would have been projected as a JSON array of
integers, contradicting N.1 and contradicting this repository's own published
schema for the same field, which says `{"type": "string", "contentEncoding":
"base64"}`.

Three vendored types carry such a field — `BlobChunk.data`,
`KeyframeFeatures.descriptors`, `Landmark.desc`. **Neither recording contains a
message of any type with an octet run**, so no gate, no golden vector and no
conformance check had a value to be wrong about. Twenty gates were green over a
rule that was dead code.

It surfaced from the other side. The client package drives the same three rules
from the generated serializer tables, which say `"t": "base64"` for exactly
those three fields; `gates/library.py` lifts every message in both recordings
with each implementation and requires agreement, and the one authored record it
adds for the rule the recordings do not exercise crashed the gateway's lifter
on the first run — `int('c')`, the third character of a base64 string being
read as a number.

Fixed by naming both spellings in one place, `webjson.OCTET_RUN`, used by both
directions of the mapping. The class is closed rather than the instance:
`gates/wire_model.py` now compares the two readings on this rule specifically —
every field the tables mark `base64` must be a field the model reads as an
octet run, in both directions, over every vendored entry — and then requires
the mapping to actually apply it, projecting two octets to `aGk=` rather than
to `[104, 105]`. Both legs fail against the pre-fix rule.

**Two lessons, and the second is the one that generalizes.** First: an
unexercised rule is not a tested rule, and a corpus that cannot reach a branch
is not coverage. Second: the defect was found by **a second implementation of
the same specification**, not by a better test of the first. The agreement gate
was written to defend the library against the gateway; it caught the gateway.
Two independent readings of one document disagree in exactly the places where
one of them guessed — which is the same argument `gates/wire_model.py` already
makes for the IDL, applied to the mapping.

Not a spec finding: the specification's IDL is unambiguous and its schemas are
correct. This was entirely ours.

## Findings from 0.2.0: the registry, and the package that is one

### 34. A published manifest lived only in memory, 2026-10-16

Found by restarting a registry that had just been published into. The three
announces came back from the journal; `resolve` answered 404. `put_manifest`
stored into `State.manifests` and nothing else, so a content manifest existed
only in the process that accepted it.

**It was not a bug until this release, which is the interesting part.** Before
0.2.0 manifests arrived from `--manifests` on disk and survived a restart by
being files; publishing one was a session-scoped addition to a set whose real
home was the filesystem. Making an empty registry the normal way to start
changed what "everything is still served after a restart" has to include, and
the code that was right under the old assumption was wrong under the new one
without being touched.

Fixed by journalling manifests as MCAP metadata records, beside the disposes
that already live there: an N.7 manifest is a JSON document rather than an IDL
type, so there is no schema to register and no channel to write it on, and
metadata is where a recording keeps things that are *about* the data. The
canonical bytes are what is stored, so a journalled manifest and a served one
cannot differ by key order.

The lesson is the one about changed assumptions rather than about manifests: a
release that changes the default state of a system has to re-ask every
question whose answer depended on the old default. "Where does this live when
nobody configured anything" was answered by `--manifests` and is now answered
by the journal.

### 35. The two halves were separate in the source and joined at runtime, 2026-10-16

0.2.0 moved the gateway into the distribution, which put a client and a server
in one package with a rule between them: neither imports the other. The
package's `__init__` was made a lazy facade so that `import spatialdds.server`
would not drag in `requests` -- the parent module runs first, and an eager
client import there would couple them.

Reading the import statements said the rule held. `sys.modules` said otherwise:
`from spatialdds import serve` loaded `requests`. The facade's `__getattr__`
answered *any* unknown attribute by importing the client half, and
`from X import Y` asks the package for the attribute before the import
machinery tries the submodule -- so every submodule probe went through the
client.

Fixed by naming the submodules in the facade and resolving those without
touching the client. The gate that matters is the one that caught it:
`gates/import_graph.py` reads the imports *and* then runs three probes in
subprocesses -- the client alone, the whole server with `requests` and
`websockets` forbidden by a meta-path hook, and `spatialdds.serve` without the
server extra installed. A static reading of a dynamic rule is a different
claim from the rule.

### 36. An empty expected value is a check that cannot fail, 2026-10-16

`docs/MYTOWN.md` was written with six command blocks and six empty output
fences, to be filled by the gate's refill pass. The gate walked all six and
reported all six green. Its comparison was `have == exp`, and both sides were
empty: the blocks had failed with a 401 and printed nothing, which is exactly
what an empty fence documents.

This is the instrument-blindness family again, in the cheapest possible
costume: not an instrument measuring its own machinery, not one blind to a
composition or a fragment, but one whose *expected value* made failure
impossible. Zero equals zero.

Fixed in both gates that compare documented output: a marked tier that prints
nothing is a failure, with the tail of stderr quoted, and `--refill` never
writes an empty fence over anything. That second guard is the older lesson
(finding 24's ad-hoc refill wrote empty fences over good output) now enforced
by the gate that validates the outputs rather than by a script somebody has to
remember.

**The general form: a check needs a non-trivial expectation before it needs a
correct one.** Any assertion whose expected value is empty, zero, or absent
should be read twice, because the degenerate case is where it stops measuring.

### 37. A documented output that no two runs can agree on, 2026-10-16

The mytown walkthrough's first step printed the ETag a publish returned.
Pinning it was impossible: a record built by `sd.records` carries `stamp: now`,
the ETag is the digest of the record's canonical form, and so the documented
value was different every run. The gate reported a diff that was not a defect.

The page now prints what it can promise and says in prose why the ETag is not
on it. **A documented output has to be a function of committed inputs**, which
is the same rule the gates follow for expected values, applied to prose.

### 38. A documented install line that installs something else, 2026-10-16

`pip install spatialdds` has been a true statement about a name and a false
statement about an artifact for as long as 0.2.0 has been unreleased: the name
resolves, and what arrives is the 0.0.1 placeholder. Every page that showed
the line was telling a reader to install something nobody wrote.

Closed in two places. Every page now prints the from-source line beside the
release line -- `pip install "./lib/spatialdds[server]"` -- so a reader on a
checkout has a line that works today. And `gates/quickstart.py` runs the
from-source line rather than the release line, into a temporary environment,
with `--no-index`: it fails any page that names a release without the
from-source line beside it, and it never reaches PyPI, so what the gate
verifies is this commit rather than whatever is published.

The rule worth keeping: **a gate that installs from the network is testing a
release, not a commit.** The checkout is the artifact under test.

### 39. The conformance suite cannot certify an endpoint that holds nothing, 2026-10-16

For the specification queue, not this repository's lane.

0.2.0 makes an empty registry the normal way to start one: `python -m
spatialdds.serve --authority registry.example --publish` serves a conformant
surface with no data in it, and records arrive later. Run the suite against
that shape and it reports **14 checks: 6 passed, 5 failed, 3 skipped**.

The five failures are all absence of data rather than any property of the
surface: *every resource validates against its published schema*, *canonical
form on the wire*, *latched state is GET with ETag, Cache-Control and 304*,
*§8.1 hello spatial in ten lines*, and *§8.4 third-party reader, published
schemas only* -- the last reporting "no latched resource to read" in as many
words. The three skips are already the suite's own UNEXERCISED form.

**The suite already has the right vocabulary and these five do not use it.** A
check with nothing to measure is unexercised, which the suite reports as a
skip and which this repository has argued for repeatedly: an absence stated is
worth more than a pass. Reported as a failure it says something false --
that an endpoint is nonconformant for having published nothing yet -- and it
makes the first ten minutes of every new registry look broken.

Suggested shape of the fix, for whoever takes it: a check that finds no
resource of the kind it needs reports UNEXERCISED with the reason, exactly as
§8.2 did before the fountain recording gained an anchor with a `manifest_uri`.
Nothing about the normative text changes; this is the runner's reporting.

Both populated shapes are unaffected and unchanged: serving the fountain, with
the publish surface off and on, is **14 checks, 14 passed, 0 failed, 0
skipped** in each, through the new `python -m spatialdds.serve` path as well as
through `tools/run_gateway.py`.

## 34. A browser could not use this gateway at all, 2026-10-18

**Found from outside, which is the whole point of the finding.** Twenty-nine
gates, a conformance suite, two recordings lifted message by message against a
second implementation — and the first real browser to meet this surface could
not issue a single credentialed request. No check here could have caught it,
because `aiohttp` enforces no CORS: it reads a response with no
`Access-Control-Allow-Origin` quite happily and sends an `Authorization`
header with no preflight at all. Every client this repository tests with is
the wrong client for this defect.

Two halves, measured against 0.5.2 from a page on `http://localhost:5173`:

```
OPTIONS /…/world/model_command/v1
  Origin, Access-Control-Request-Method: POST
  Access-Control-Request-Headers: authorization,content-type
→ 405 Allow: GET,HEAD,POST

GET /…/world/entity/v1            (public, no credential)  → 200, ACAO: *
GET /…/world/entity/v1            (public, credential)      → preflight 405, never asked
GET  an operator-scoped resource  (valid credential)        → 200, no ACAO at all
```

**`Authorization` is not CORS-safelisted**, so a cross-origin request carrying
one preflights, and no `OPTIONS` was routed. So **N.6 was unreachable from a
browser for every access class** — a command always carries a credential, so
the preflight is forced by the *request* and no reclassification of the
resource can avoid it. And the converse bit a careful client: a page that
volunteered a credential at a *public* lane, on the reasonable theory that a
credential is harmless where it is not required, turned a working read into a
silently empty one.

The second half was a reasoning error rather than an omission, and it is the
more interesting one. `_cache_headers` refused an origin to any gated resource,
on the stated grounds that its credential "must not be invited to attach
ambiently". Right about the risk, wrong about the mechanism:
`Access-Control-Allow-Credentials` is what issues that invitation, this package
sets it nowhere, and a `Bearer` token the page holds and chooses to attach is
the opposite of ambient. Refusing the origin never kept a credential out of
anything. It made an authorized `200` unreadable by the client that had
authorized itself.

Fixed in 0.5.3: every path answers `OPTIONS`, an authorized operator-scoped
response echoes the asking origin with `Vary: Origin, Authorization`, and
`Allow-Credentials` stays absent everywhere. `gates/cors_preflight.py` holds
sixteen checks and says in its own docstring what it cannot claim — it asserts
the headers a browser consults, never the browser's decision, because the
harness cannot make that decision for it.

**And the specification gap is separate, which is why it was routed
separately.** Nothing in Appendix N required any of this. A conforming endpoint
could omit every part of it and still pass, which is what happened here: this
gateway had no defect against the text, and its flagship client could not use
it. So the ask is not "permit this" — nothing forbade it — but *require a
browser-facing binding to state its preflight behaviour*, most naturally an
implementation-requirements note. Three sentences' worth of substance are
drafted in `SpatialDDS-demo`'s `docs/browser-credential-finding.md`, including
the one that is a **client** requirement: do not send a credential to a
resource whose access class does not require one.

The remaining half of that filing is not ours and not fixed here: a browser
cannot put a credential on a WebSocket handshake at all, which N.4 requires
and whose alternatives it forbids. That is blocker A, it is the spec agent's,
and subprotocol carriage is the likely answer.

### 34b. The same finding, one release later, 2026-10-18

**0.5.3 fixed it in the three places that share a helper and missed the two
that do not** --- and it was found the same way again: by curling the running
demo after the release, not by the sixteen checks written for it.

`post_command` builds its `202` by hand, and `_problem` builds every N.8
document by hand with `Cache-Control: no-store` and nothing else. Neither went
through `_cache_headers`, so neither named an origin. Consequences, in order
of how bad they are:

* **An N.8 problem document was unreadable by the client it was written for.**
  A cross-origin `401`, `409` or `400` rejects at the `fetch`, so the page
  gets no status and no body. N.8 exists to make a refusal a *stated reason*
  rather than a number; a reason the client cannot read gives up exactly that.
* A command's own reply was unreadable, so a page saw a failure for a command
  that had published successfully. Worse than a plain error, because the
  system and the client disagree about what happened.

Fixed in 0.5.4 by a one-rule middleware --- if a response names no origin and
the request had one, echo it with `Vary: Origin` --- rather than by threading
the origin through a fourth and fifth call site. **That choice is the finding.**
Explicit-at-every-site is the more careful-looking design and it is the one
that failed: there were five sites, the obvious three shared a helper, and the
two that did not were invisible precisely because they were written by hand.
A rule that cannot be forgotten beats a convention that must be remembered,
and `_cache_headers` keeps deciding cacheability and reach together where it
already acts, so the pairing argument it was built on is untouched.

The gate gained the two checks that would have caught this, and they are shown
failing against 0.5.3 exactly as published. Note what they are: assertions
about *the response a handler did not build through the shared path*. The
sixteen checks before them all examined responses that did.

## Findings from the demo.spatialdds.org deploy, 2026-10-23

Found by standing up the public sandbox on 0.5.4 from PyPI in the persistent
shape (`--demo --publish --journal`), rehearsed locally first and then
exercised on the box. Nothing here was patched on the box.

### 40. The journal has no compaction, and every write rewrites all of it

Measured, 0.5.4. `Journal` keeps every accepted entry for the life of the
file and rewrites the whole MCAP on each write (`_flush`, temp file then
`os.replace`) — which is why the file is a complete, summarised recording at
every instant, and is the right call for that property. Neither rotation nor
compaction exists.

| state | bytes |
| --- | ---: |
| first write (the embedded IDL dominates) | 47,038 |
| per `examples/publish_duck.py` run (one manifest record + one announce sample) | ~1,128 |
| per withdraw (one dispose record) | 245 |

Five republishes of the same duck added five copies of the same two entries
(47,283 → 52,928 bytes), and replay walked all 13 to reach a state holding
one duck. At sandbox rates this is a future problem; the costs are linear in
history rather than in state, both on disk and on every write.

*Smallest change:* journal compaction that emits a closed, indexed MCAP
snapshot of the current state — the latest sample per instance, live
manifests, and the disposes still needed to explain an absence — written over
the same temp-and-rename path, so the every-instant-valid property survives.

### 41. A published manifest's served bytes change at the first restart

Found by the persistence proof: across a restart, six of seven served paths
were byte-identical; the duck's resolve document was not. Same JSON value,
different spelling — `158.0` → `158`, `2.0` → `2`:

```
40c40
<             158.0
---
>             158
42c42
<         "circle_radius_m": 2.0,
---
>         "circle_radius_m": 2,
```

The journal stores `webjson.canon(doc)` (finding 34), but the accepting
process serves the PUT body as received. So the property finding 34 claimed —
a journalled manifest and a served one cannot differ — holds for key order and
not for number spelling, and the first restart is when a client's ETag stops
matching a document whose content never changed. Reproduced on the live
endpoint, identically.

*Smallest change:* store `canon(doc)` at accept, so the accepting process
serves the same bytes replay will.

### 42. The startup banner writes a pinned operator token to the system log

With `--token` given, the banner still prints `bearer token`, `export TOK=` and
`export SPATIALDDS_TOKEN=` with the value, so under systemd a pinned token is
in `journalctl -u <unit>` — readable by the `adm` group, retained beyond any
rotation of the token itself. Printing it makes sense when the token was
generated (nobody else knows it); it does not when the operator supplied it.

*Smallest change:* print the token only when it was generated; when given,
print that it was given.

### 43. `examples/hello_spatial.py` cannot reach a TLS endpoint

It turns the topic URL into a WebSocket URL with `.replace("http://", "ws://")`,
which leaves `https://` untouched; against `https://demo.spatialdds.org` it
ends in `InvalidURI: ... scheme isn't ws or wss`. Its default is the one
example default still pointing at localhost, so flipping it to the live
endpoint would ship the front page's first example broken.

*Smallest change:* `.replace("http", "ws", 1)`.

### 44. Withdrawing content leaves its manifest resolvable

`DELETE` on the announce disposes it (the dispose and its reason reach a live
subscriber), but there is no route that withdraws the manifest: after the
withdraw, `resolve` still answers 200 for the duck. That is defensible — a
manifest is an N.7 document, not an instance — but it means "withdraw" on this
surface is half a withdrawal, and only a deliberate reset removes the rest.
Worth a sentence in docs/PUBLISH.md, or a `DELETE` on the resolve path.
