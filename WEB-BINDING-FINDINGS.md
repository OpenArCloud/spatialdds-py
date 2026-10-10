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
