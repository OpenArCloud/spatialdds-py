# What's on the wire, and how to read it

Moved out of the README so the front page could be for someone who has not
decided to trust this yet. Nothing here is rewritten: these are the same
sections, intact, in the order they were written.

See also [`EVIDENCE.md`](EVIDENCE.md) for the gates and the conformance runs,
and [`LAYOUT.md`](LAYOUT.md) for the directory map.

## What it serves

Any typed SpatialDDS 1.8 MCAP whose schema records carry omgidl. The recording
is the input and the interface. Nothing here is specific to what produced it.

```sh
python3 tools/run_gateway.py --mcap /path/to/your.mcap
```

**A complete XCDR2 implementation, read and write.** `spatialdds_web/xcdr2.py`
carries a `Reader` and a `Writer`, in one file so the wire rules are stated once
and cannot drift into disagreeing about alignment or delimiters. Both are
validated against the reference implementation rather than against each other:
the reader decodes all 15,676 messages of the retail recording to JSON
identical to a CycloneDDS-based decoder's, and the writer re-encodes all 15,676
**byte-identically to what CycloneDDS originally wrote**.

Two wire truths it was expensive to learn, both recorded permanently in that
module: XCDR2 caps primitive alignment at four bytes, not at the type's size,
so an 8-byte double is 4-aligned; and a delimited aggregate's DHEADER is
patched in place rather than written to a sub-buffer, because alignment is
measured from the start of the message and a sub-buffer pads differently than
its contents will sit.

That makes this repository the seed of two things beyond the gateway: the
binding's client library, and the future publish path. `tools/author_anchors.py`
is the first use of the writer, and the shape of its input is evidence for what
a write API can honestly accept.

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

**Declared coverage states a shape; derived coverage encloses one.** The
recording's service announce carries a circle, because a person stated its
centre and radius in the input — that is the case §3.3.4 prefers the circle
form for, a footprint someone chose. Coverage the gateway computes from the
resources it happens to serve is a different thing, and uses the
parameter-free enclosure: the bounding box of a point set involves no invented
decision, whereas a circle over those same points would require picking a
centre and a radius that nothing declared. One rule covers both every time
coverage is computed rather than authored.

**And declared beats derived.** When the recording carries an announce, the
gateway serves the producer's own identity and footprint; synthesis is the
fallback for a recording that carries none. The topic list stays derived
either way, because the gateway knows what it actually serves. A gate asserts
that the declared footprint encloses the one the gateway would have derived: a
declared coverage smaller than the resources it covers is an under-report, and
that is the one direction coverage must never err.

**Anchor-attached assets landed.** The 1.8 revision (spec `297c433`) gave
`GeoAnchor` an optional `manifest_uri`, mirroring `ContentAnnounce`'s member
exactly: same type, same name, empty-is-absent, no `has_` guard, appended under
APPENDABLE so older anchors are unchanged in meaning and on the wire. This
recording exercises both legs — the plaque anchor names the label's content
manifest, and the other four carry `""`, because absence is data and a member
with no served counter-example is a member nobody has checked.

That closes 1.9 candidates entry 2 and unblocks the conformance suite's §8.2
scenario, which had skipped since it was written: it reads "fetch an anchor's
asset by reference", and until the revision the reference had nowhere to live.

**The sample's content host and its URI authority coincide by construction.**
The recording's URIs are `spatialdds://demo.spatialdds.org/...`, and
`demo.spatialdds.org` is also the endpoint that serves the content — which is
what lets the sample resolve its own URIs end to end. Real deployments
routinely split the two: the authority that owns a namespace and the host that
serves a given asset need not be the same organisation, let alone the same
machine. A federated demo with two authorities, where resolution crosses a host
boundary, is future work and is not what this recording shows.

Note that `authority` is deliberately not `org`: the publisher is OpenArCloud,
the resolving authority is the demo host. §7.5.2 makes the authority a DNS name
a client resolves against, so it is a deployment fact and not an identity.

The reference chain, with every link gated and none of them a fixture:

```
GET the content announce  ->  manifest_uri (spatialdds://…)
  ->  GET /.well-known/spatialdds/resolver   ->  https_base
    ->  GET {https_base}?uri=…               ->  the content manifest
      ->  assets[].uri and assets[].hash
        ->  samples/assets/fountain-marker.gltf, compared byte for byte
```

The middle two steps used to be a gate-local mapping from the URI's last path
segment to a committed file, labelled a fixture because that is what it was: an
undocumented construction rule is a dialect. It is gone. The gateway implements
§7.5.2 resolution for the manifests it holds, and
`gates/content_resolution.py` learns the resolve URL the way a client does, by
reading the advertised metadata. See [`RESOLUTION.md`](RESOLUTION.md).

The asset URLs point at `raw.githubusercontent.com` and resolve only once the
commit is pushed, so the gate byte-compares against the committed file and says
so; `--remote` fetches them for real as the post-push check.

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
[`WEB-BINDING-FINDINGS.md`](../WEB-BINDING-FINDINGS.md). Two of those findings
matter more than the rest, because two normative rules assume a DDS domain
behind the endpoint and so cannot hold for the standalone shape the conformance
suite exists to certify.

There is no command lane. A POST surface needs something to accept a verb for,
and a recording has nothing to command.
