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

## The two instrument-defect classes, consolidated, 2026-10-11

Owed since the instances crossed six. Both classes are already described where
they were found — the first at "A defect class, named because it has now
happened twice", the second at finding 23 — and a class scattered across dated
sections is not something anyone can apply. This is the roll-up: two classes,
their instances, and one sentence each.

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

### What both classes have in common

The instrument was never tested against the thing it claims to measure. The fix
is the same move in both: make the measured thing go wrong, and confirm the
instrument says so.

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
