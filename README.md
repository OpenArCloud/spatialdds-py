# spatialdds-web

The SpatialDDS web binding's standalone reference implementation: a recorded
SpatialDDS stream served over HTTPS GET and a WebSocket, with no DDS anywhere
in the path. A client with `fetch` and a WebSocket reads typed SpatialDDS
without a DDS library, a domain, or a discovery handshake.

This is also where the binding's client library will live.

```sh
pip install aiohttp websockets jsonschema mcap
python3 tools/run_gateway.py
```

Then, in another terminal, the whole client:

```sh
$ python3 samples/hello_spatial.py
RESTRICTED       Keep-Out Zone        821266db-5c13-414b-91e0-7d2d4c2c307a
MONITORING       Queue Region         ac869071-2efa-47d2-bf77-fa0c7248593e
latched set complete: 2 zone(s)
```

`samples/hello_spatial.py` is ten lines of `requests` and `websockets`. It asks
the discovery endpoint what is here, picks a topic out of the service manifest
it gets back, and subscribes to that topic's own URL. Only the base URL is
hardcoded: the path is followed, not constructed.

## What it serves

Any typed SpatialDDS 1.8 MCAP whose schema records carry omgidl. The recording
is the input and the interface. Nothing here is specific to what produced it.

```sh
python3 tools/run_gateway.py --mcap /path/to/your.mcap
```

**The recording describes itself, and this reads it.** The wire layout comes
from the IDL embedded in the file's schema records. The instance key of a
latched resource is the member that IDL marks `@key`. The resource paths are
the topic names. Enum values are read from their declared `@value`, which is
not their position: `CovarianceType` runs 0, 3, 6, 9, 12, and decoding by
position would mislabel every covariance in the file.

One thing a recording does not carry is QoS. Nothing in an MCAP says a topic
was TRANSIENT_LOCAL, and the binding's shape depends on that split, so
durability is declared per type in `spatialdds_web/gateway.py` from the
specification's QoS table. That gap is a finding.

A latched topic is a GET resource at the on-bus topic name with `spatialdds/`
removed, so `spatialdds/<domain>/regions/spatial_zone/v1` is served at
`/<domain>/regions/spatial_zone/v1`, and one instance at `.../<zone_id>`. A
WebSocket to the collection URL replays the latched set, sends exactly one
`end_of_replay` carrying the count, then streams live. That marker is what the
web binding adds: on a bus "you now hold the whole latched set" is implicit in
the QoS, and over a WebSocket a client cannot otherwise tell a slow replay from
a quiet topic.

**Configuration is public, observation is not.** Zones, crossing lines, frame
transforms and georeferences describe a building and contain no person, so they
are served without auth and are cacheable. Entities, tracks, events and camera
detections are observations of people in that building, so they are
bearer-gated and `no-store`. A gate checks both directions and checks that
nothing operator-scoped can be served cacheable.

## Decoding: parse the IDL, cross-check with the tables

This is the binding's decoding path, and it is normative rather than a local
preference. Two things settled it, both measured rather than reasoned about.

**Wire layout comes from the IDL.** Not from the published serializer tables:
they describe `double[3]` and `sequence<string,32>` identically, as
`{"t":"array"}`. That is right for emitting JSON, where both become JSON
arrays, and fatal for reading CDR, where one carries a length prefix and the
other does not. The distinction the tables erase is exactly the one the wire
needs, so anything that reads SpatialDDS off a bus or out of a recording has to
read the IDL. *This is a generator defect in the specification repository, not
a workaround here:* the generator knows the collection's nature when it walks
the AST and does not record it. Finding 8.

**The IDL to parse is the original, not a recording's flattened copy.** An MCAP
schema record says in its own preamble that it is "a single self-contained
translation unit", and `idlc -l py` segfaults on it while compiling the
specification's `events.idl` without complaint. Reproduced on all three
distinct payloads in the sample recording, with comments stripped and intact;
the `types.idl` section alone compiles. So a consumer cannot rebuild types from
a recording with the reference toolchain, which is the main thing
self-description is for. Finding 9.

What this repository therefore does: it parses the IDL text the recording
carries to build a wire model — field order, array bounds against sequence
bounds, `@key`, `@value`, `@extensibility` — and then checks that model against
the published serializer tables, which were generated upstream by a different
program walking a different representation. `gates/wire_model.py` requires the
two readings to agree on every type's kind, field order, enum members and union
cases before anything is served. The tables are the cross-check; they are not
the source.

The one thing the tables are authoritative for is a union's empty branch.
`CovMatrix` declares `case COV_NONE: uint8 none;`, a real byte that is on the
wire and must be consumed, while `MAPPING.md` says an empty branch is
`{"type":"COV_NONE"}` with no member. That the placeholder is padding rather
than information is recorded only in the generated tables, as a `null` branch
type, and no rule over the IDL recovers it. Finding 12.

## A second recording: authored anchors

`samples/fountain-anchors.mcap` is the content machinery end to end. Five
`GeoAnchor`s at the Littlefield Fountain on the UT Austin South Mall, each with
directional covariance, plus two `ContentAnnounce`s whose coverage circles sit
on two of them, resolving through content manifests to two small committed
assets.

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap
```

**It is authored, not captured, and says so.** These anchors are declared data,
the way the retail recording's zones are configured data — nobody surveyed this
fountain for us. Positions are approximate. Every value the recording carries
comes from `samples/fountain-anchors.input.json`;
`tools/author_anchors.py` serializes that file and does no arithmetic on it,
which is what lets `gates/golden_anchor.py` take its expected values from the
input rather than from the tool that wrote the output.

`confidence` is derived in the input from the covariance it summarises,
`1/(1+sigma_max)`, so the scalar and the matrix cannot contradict — which is
what `GeoAnchor`'s own comment asks for. A gate checks the relation holds, and
that confidence falls as covariance grows across the set.

**Content is positioned by its own pose and coverage.** That is the
content-record model this sample succeeds: an SCD record carried its own
geopose and never referenced an anchor, and `ContentAnnounce` with a
`manifest_uri` is that record's typed successor, working as specified. Each
announce carries a coverage circle — the circle form, because §3.3.4 says a
producer SHOULD prefer it "so consumers can recover the exact footprint" rather
than hand-computing a box.

Anchor-attached assets are a separate capability, and 1.8 has no field for it:
no anchor type can reference content. That is written up as entry 2 of the
1.9 candidates, and it is why the conformance suite's §8.2 scenario still
skips — the scenario reads "fetch an anchor's asset by reference", and the
reference has nowhere to live yet.

The reference chain that does work, with every link gated:

```
GET the content announce  ->  manifest_uri (spatialdds://…)
  ->  the content manifest  ->  assets[].uri and assets[].hash
    ->  samples/assets/fountain-marker.gltf, compared byte for byte
```

One link in the gate is a fixture and is labelled as one: this endpoint
publishes no resolver, so `gates/content_resolution.py` maps the URI to a
committed manifest itself. That mapping is a test fixture, not a wire
convention — an undocumented construction rule is a dialect. The asset URLs
point at `raw.githubusercontent.com` and resolve only once the commit is
pushed, so the gate byte-compares against the committed file and says so.

The authoring tool needs a specification checkout for the IDL it embeds
(`--idl`), because the recording carries its own flattened IDL exactly as the
retail one does. Serving the result needs no IDL at all.

## The contract is not ours

Paths, the four event names, the envelope fields, the ordering contract and the
exact `end_of_replay` shape are pinned in the specification repository's
`web-binding/conformance/README.md`. The JSON comes from schemas and
serializer tables generated there, vendored under `webjson/` and pinned by
content digest in `webjson/PROVENANCE` along with the recording.

Where the pinned surface could not be implemented as written, this gateway
follows it and the gap is written up:
[`WEB-BINDING-FINDINGS.md`](WEB-BINDING-FINDINGS.md). Two of those findings
matter more than the rest, because two normative rules assume a DDS domain
behind the endpoint and so cannot hold for the standalone shape the conformance
suite exists to certify.

There is no command lane. A POST surface needs something to accept a verb for,
and a recording has nothing to command.

## Evidence

```
$ python3 gates/run_all.py

  PASS     wire_model          the recording's IDL and the published tables agree
  PASS     golden_json         the JSON projection matches the authored vectors
  PASS     decode_fidelity     every message decodes and the counts are the recording's
  PASS     determinism         two builds serve byte-identical latched resources
  PASS     replay_order        latched replay, exactly one end_of_replay, then live
  PASS     removal_parity      every removal is a terminal sample then a dispose
  PASS     schema_fidelity     the endpoint publishes and obeys the generated schemas
  PASS     access_classes      configuration is public, observation is bearer-gated
  PASS     golden_anchor       one anchor field by field against the authoring input
  PASS     anchor_serve        anchors served unmodified, and discovery finds them
  PASS     content_resolution  a served reference ends in a real committed file

11 gates: 11 passed, 0 failed, 0 skipped
```

Expected values come from the recording, not from this gateway: per-topic
counts and the total from its own `summary.json`, the file digest from
`webjson/PROVENANCE`, the retirement count counted directly over the decoded
messages, and the replay counts from the instances in force when the window
opened. Asking the gateway how many samples it found and then checking it
served that many would pass for any gateway, including a broken one.

| gate | what it shows |
|---|---|
| `wire_model` | the model parsed from the recording's embedded IDL agrees with the published serializer tables on every type's kind, field order, enum members and union cases, for all 71 shared types |
| `golden_json` | the JSON projection reproduces the four vectors the specification authored by hand, and the ECMA-262 number rule is checked as text, because comparing parsed values cannot see it |
| `decode_fidelity` | all 15,676 messages decode, every per-topic count matches the recording's summary, and every decoded sample validates against its published schema |
| `determinism` | two independent builds serve byte-identical latched bodies and identical ETags, and the conditional GET contract holds in both directions |
| `replay_order` | every replayed sample precedes exactly one `end_of_replay`, whose count matches both the samples sent and the instances in force at the window's start |
| `removal_parity` | all 43 removals arrive as a terminal `RETIRED` sample immediately followed by a `dispose` with a reason, and no disposed key is still served over GET |
| `schema_fidelity` | the schemas endpoint serves 112 files identical to the vendored copies, the vendored set matches its recorded digest, and everything served validates against them |
| `access_classes` | public resources are served without auth and cacheable, gated ones are refused without a token and never cacheable, on both GET and WebSocket |
| `golden_anchor` | one anchor compared field by field against the authoring input, plus the checks that only make sense across the set: confidence is the monotone summary of covariance that `GeoAnchor` requires, it falls as covariance grows, no two anchors share a position, and the spread is fountain-sized |
| `anchor_serve` | the anchor recording is served unmodified: both types latched and public, all seven instances addressable by their percent-encoded `@key`, every ETag a content digest, every instance schema-valid, and a geohash query over the anchors' own cell returning the service while San Francisco returns nothing |
| `content_resolution` | a served `ContentAnnounce` resolves through `manifest_uri` to a content manifest to a committed asset, byte-compared; and every announce's coverage circle is centred on one of the recording's anchor positions |

During development the decoder was additionally checked against a
CycloneDDS-based reader on every message in the recording: all 15,676 decoded
and projected to byte-identical JSON. That comparison needed a DDS runtime and
so is not a gate here; what replaced it is `wire_model` plus per-message schema
validation, which need nothing but this repository.

## Standing it up from a harness

The backend-blind conformance suite takes a base URL and a bearer token, so it
needs to start one of these itself.

```sh
python3 tools/run_gateway.py --mcap <file> --port 8787 --token <token>
```

Both are explicit so nothing has to be scraped from the output; without
`--token` one is generated and printed. The gateway refuses a port already in
use rather than failing softly. **Check readiness by confirming the listening
process is the one you started**, not by fetching a URL until it answers: a URL
that answers only tells you something is there. In-process is simpler where the
harness is Python, and `gates/harness.py` shows it: bind port 0 and be handed
the port once the socket is listening, so there is nothing to poll.

## Layout

```
spatialdds_web/   idlmodel (wire model from embedded IDL), xcdr2 (reader+writer),
                  webjson (canonical JSON), gateway (state, discovery),
                  gw_http (the pinned HTTP and WebSocket surface)
webjson/          schemas, serializer tables, golden vectors, MAPPING, PROVENANCE
samples/          two recordings, their inputs, assets, manifests, and the
                  ten-line client
gates/            everything above
tools/            run_gateway.py, author_anchors.py
```

## Status

The recording is SpatialDDS 1.8, produced by a SceneScape adapter and published
in [`OpenArCloud/spatialdds-scenescape`][sc]. There is no code dependency on
that adapter in either direction; the MCAP is the whole interface. A live MQTT
or DDS source is a later backend behind the same seam.

Not soaked. Nothing here tells you about memory over hours, many concurrent
subscribers, or a recording larger than memory: the whole file is decoded at
startup and held.

[sc]: https://github.com/OpenArCloud/spatialdds-scenescape
