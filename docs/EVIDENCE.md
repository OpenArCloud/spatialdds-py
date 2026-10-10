# Evidence

Every claim the README makes is gated, and the gate manifest below is this
repository's output rather than a sentence written beside it. Moved here intact
from the README.

See also [`WIRE.md`](WIRE.md) for the wire format and the decoding contract,
[`LAYOUT.md`](LAYOUT.md) for the directory map, and
[`RESOLUTION.md`](RESOLUTION.md) for the `spatialdds://` resolution
characterization.

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
  PASS     examples            every example runs and prints what examples/ documents

12 gates: 12 passed, 0 failed, 0 skipped
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
| `access_classes` | public resources are served without auth, cacheable and CORS-open with their ETag and access class exposed, their 304s carrying the CORS header too; gated ones are refused without a token, never cacheable and never CORS-open, on both GET and WebSocket |
| `golden_anchor` | one anchor compared field by field against the authoring input, plus the checks that only make sense across the set: confidence is the monotone summary of covariance that `GeoAnchor` requires, it falls as covariance grows, no two anchors share a position, and the spread is fountain-sized |
| `anchor_serve` | the anchor recording is served unmodified: both types latched and public, all seven instances addressable by their percent-encoded `@key`, every ETag a content digest, every instance schema-valid, and a geohash query over the anchors' own cell returning the service while San Francisco returns nothing |
| `content_resolution` | a served `ContentAnnounce` resolves through `manifest_uri` to a content manifest to a committed asset, byte-compared; and every announce's coverage circle is centred on one of the recording's anchor positions |
| `examples` | every example in `examples/` is executed against a locally served recording and its documented output is diffed against what it prints: five scripts, four shell fences, two browser snippets under `node`. An example it cannot run it reports as a failure, not a pass |

During development the decoder was additionally checked against a
CycloneDDS-based reader on every message in the recording: all 15,676 decoded
and projected to byte-identical JSON. That comparison needed a DDS runtime and
so is not a gate here; what replaced it is `wire_model` plus per-message schema
validation, which need nothing but this repository.

## Candidate specification clarifications, flagged not assumed

Three places where this endpoint had to decide something the appendix does not
say in so many words. Each is written down here and queued for the spec rather
than folded into the appendix as though it were already there.

**Public-cacheable implies CORS-open — candidate N.4.** Public-cacheable
resources are served with `Access-Control-Allow-Origin: *` and operator-scoped
resources are not, set in the same branch of `gw_http._cache_headers` that sets
the cache header, so the two cannot drift apart. The reasoning is that a
resource served to anyone without a credential has no origin worth refusing,
and a resource gated by a bearer token must not invite a browser to attach one
ambiently. N.4 defines the two access classes by auth and cacheability and says
nothing about reach, so the pairing is an inference — a sound one, and still an
inference. Appendix N should say whether it holds.

**Exposed headers are part of the claim.** `Access-Control-Expose-Headers`
carries `ETag` and the three `X-SpatialDDS-*` diagnostics, because without it a
cross-origin reader gets the body and not the ETag or the access class
describing it: headers served and unreadable, which is this repository's named
defect class in small. The diagnostic headers are non-normative by earlier
ruling, so exposing them invents nothing; the ETag is not, and a browser client
that cannot read it cannot do a conditional GET.

**Known gap in the same area, not fixed here.** A JavaScript client that sets
`If-None-Match` itself triggers a CORS preflight, and this endpoint serves no
`OPTIONS` handler, so that request fails cross-origin. The browser's own HTTP
cache revalidates without a preflight and is unaffected, which is why the
browser examples work; explicit conditional GETs from script do not. Whether
N.4 should require `OPTIONS` on public-cacheable resources is part of the same
question, and adding the handler was outside a batch told to change no gateway
behaviour except where named.

**`ttl_sec` versus `max-age` — candidate N.4.** §7.5.2 says a fetched
manifest's own `ttl_sec` governs its cache lifetime; N.4 sets cache behaviour by
access class. This endpoint serves every public resource with
`max-age=300` from one constant, while the committed content manifests carry
`ttl_sec: 86400`. The two only meet on a resolver response, which this endpoint
does not serve, so nothing is wrong today — see
[`RESOLUTION.md`](RESOLUTION.md), which is where it would start to matter.

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

## Status

The recording is SpatialDDS 1.8, produced by a SceneScape adapter and published
in [`OpenArCloud/spatialdds-scenescape`][sc]. There is no code dependency on
that adapter in either direction; the MCAP is the whole interface. A live MQTT
or DDS source is a later backend behind the same seam.

Not soaked. Nothing here tells you about memory over hours, many concurrent
subscribers, or a recording larger than memory: the whole file is decoded at
startup and held.

[sc]: https://github.com/OpenArCloud/spatialdds-scenescape
