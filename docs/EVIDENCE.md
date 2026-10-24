# Evidence

Every claim the README makes is gated, and the gate manifest below is this
repository's output rather than a sentence written beside it. Moved here intact
from the README.

See also [`PUBLISH.md`](PUBLISH.md) for the implementation-defined write
surface, [`WALKTHROUGH.md`](WALKTHROUGH.md) for a VPS and a duck published and
followed back, [`WIRE.md`](WIRE.md) for the wire format and the decoding contract,
[`LAYOUT.md`](LAYOUT.md) for the directory map, and
[`RESOLUTION.md`](RESOLUTION.md) for the `spatialdds://` resolution
characterization.

## Evidence

```sh
python3 gates/run_all.py
```

```
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
  PASS     quickstart          the README's quickstart runs when pasted verbatim
  PASS     golden_publish      a published record is indistinguishable from a replayed one
  PASS     publish_reject      every refusal is a status and a sentence, and stores nothing
  PASS     journal_replay      restart is the same state, and a backup is a recording
  PASS     publish_live        a publish reaches a subscriber already on the socket
  PASS     publish_offswitch   with publishing off, reads are byte-identical and writes are 405
  PASS     publish_duck        a published record followed from PUT to fetched bytes
  PASS     publish_anchor      a published anchor is an anchor, and the content it names resolves
  PASS     library             the spatialdds client package reads, publishes and refuses as documented
  PASS     import_graph        one gateway, two halves, and no import between them
  PASS     fresh_start         a registry that starts empty: types from the pin, state through a restart
  PASS     serve_demo          the packaged demo serves, and the examples do not know the difference
  PASS     builders            the record builders produce what the hand-authored records say
  PASS     deploy_image        the image's own instructions serve the endpoint from the new layout
  PASS     commands_n6         the N.6 command surface accepts, declines with a reason, and advertises only what it serves
  PASS     live_backend        apply_sample and notify are two halves, and the write surface is a separate switch
  PASS     cors_preflight      a credentialed cross-origin request is preflighted and its answer names the asking origin

29 gates: 29 passed, 0 failed, 0 skipped
```

The newest of them, `library`, is the only one with a second implementation to
compare against rather than a recording. The `spatialdds` client package in
`lib/spatialdds` cannot import this repository, so it drives Appendix N.1's
three lift rules from the generated serializer tables while the gateway drives
them from the IDL a recording embeds; the gate lifts **every one of the 15,684
messages in both recordings** with each and requires them to agree, then adds
one authored record for the rule neither recording exercises. That comparison
found a defect on its first run, and in the gateway rather than in the library:
finding 33.

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
| `access_classes` | public resources are served without auth, cacheable and CORS-open with their ETag and access class exposed, their 304s carrying the CORS header too; gated ones are refused without a token, never cacheable and never CORS-open, on both GET and WebSocket. The two §7.5.2 resolution routes are checked here as well, since they are access-classed resources that are not topics and the topic sweep cannot see them: public, cacheable, CORS-open, and unmoved by a bad credential |
| `golden_anchor` | one anchor compared field by field against the authoring input, plus the checks that only make sense across the set: confidence is the monotone summary of covariance that `GeoAnchor` requires, it falls as covariance grows, no two anchors share a position, and the spread is fountain-sized |
| `anchor_serve` | the anchor recording is served unmodified: both types latched and public, all seven instances addressable by their percent-encoded `@key`, every ETag a content digest, every instance schema-valid, and a geohash query over the anchors' own cell returning the service while San Francisco returns nothing |
| `content_resolution` | the full §7.5.1 chain with no fixture in it: read the authority's resolver metadata, follow the advertised `https_base`, resolve each announce's `manifest_uri` over HTTP, check the resolved document is the URI asked for and is the committed manifest byte for byte, check its `max-age` is the manifest's own `ttl_sec` and not the endpoint's default, then byte-compare the asset. Plus both refusals: another authority's URI and an unheld URI of its own, each a `404` naming which. And every announce's coverage circle is centred on one of the recording's anchor positions |
| `examples` | every example is executed against a locally served recording and its documented output is diffed against what it prints: eight scripts, five shell fences, two browser snippets under `node`, and one documented *refusal*. Plus an **isolation mode** — each script run alone against its own fresh endpoint, because a script that works only mid-sequence passes a document-order walk and fails a reader. Plus the mismatch sweep — each script run against every self-advertising configuration it did not ask for, which must refuse in a sentence and never in a traceback. An example it cannot run it reports as a failure, not a pass |
| `golden_publish` | one service announce and one content record published and checked whole, every expected value from `samples/published-records.input.json` and none from the gateway: the response is the served canonical form field for field, its ETag is the digest of that form, a reader's GET returns the same bytes, it validates against the vendored schema, a schema-valid body carrying members the type does not declare comes back without them, **two announces in one scene both appear in search and in a geohash query over it** — the regression gate for a read-side defect only a writer exposed — and a published manifest resolves through §7.5.2 with the `ttl_sec` rule intact |
| `publish_reject` | seven refusal classes, each a status and a problem+json sentence — no token, wrong token, non-record body, foreign authority, unpublishable topic, key/URL mismatch, disposing nothing — and the registry untouched after all seven. Plus why the authority check can never have nothing to check: a bound endpoint always has a host |
| `journal_replay` | three publishes and a dispose, then a restart from the journal serving byte-identical bytes; the disposed record stays gone; the journal is a valid MCAP whose schemas are `omgidl` under their own names; and a gateway given only the journal learns its types from it |
| `publish_live` | a subscriber already on the socket receives a publish as a `sample` and a dispose as a `dispose`, on the same envelope a replayed sample has, and a later subscriber replays the published record as latched state |
| `publish_offswitch` | the one that matters: with the surface off, eight reads are byte-identical to an endpoint built without it, the write verbs are `405` problem documents, an attempted DELETE leaves its instance untouched, and a subscription still terminates by itself |
| `publish_duck` | the duck followed from `PUT` to fetched bytes: the committed asset's size and digest match what the authored input declares, the manifest and announce are accepted and served as authored, the content collection holds the duck **and** both recorded content records, the URI resolves with the manifest's own `ttl_sec` as `max-age`, and the ref's bytes are byte-identical to `samples/assets/duck.gltf`. Plus the asset route's containment rule, checked over HTTP and over names a request cannot carry; plus the VPS loop — the manifest derived from the specification's own `manifests/v1.8/vps_manifest.json`, resolved with its TTL, and its announce findable by search |
| `publish_anchor` | a published anchor is an anchor: every served field equals the authored record, its ETag is the digest of its canonical form, it validates against the vendored schema, and its `confidence` really is the monotone summary of its `cov` — so a record this endpoint accepted cannot contradict its own claim level. It stands beside the recording's five with those five byte-unchanged; its `manifest_uri` resolves and the ref fetches the committed asset byte for byte, which is the write-side twin of §8.2; and the refusals include the authority check's **anchor leg**, which had no data to exercise it until the revision gave anchors a URI to carry |
| `quickstart` | the README's front page, `docs/MYTOWN.md`, `docs/WALKTHROUGH.md` and `examples/curl.md` executed **as text a reader pastes**, with no normalising: each command fence goes to the shell byte-for-byte, on the port the serve line actually names, **with nothing injected — not even the environment**: an earlier version prepended the page's export fence, which is why it was green while all ten command blocks failed in a fresh shell (finding 29). Every marker must sit on a fence holding its command alone, so a fence mixing prompt and output cannot return; every tier must name the recording the serve line serves; and a busy port is a FAIL naming the occupant rather than a skip. As of 0.2.0 it also performs the install each page names, from the checkout with `--no-index`, and checks that every internal link on the page resolves. The first version of this gate stripped the prompt and expanded `$BASE` before running, and passed while the page was unrunnable — finding 24 |

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

**`ttl_sec` versus `max-age` — landed, no longer a candidate.** This was
flagged here as a prospective N.4 clarification and is now specification text:
SpatialDDS-spec `169fabd` amends N.4 so that a public-cacheable manifest's own
`ttl_sec` (§7.5.2) governs its `max-age` in place of the access class's
default, and grants no lifetime to an operator-scoped resource whatever
`ttl_sec` it carries. Both halves are implemented in `_cache_headers` and gated:
the resolve route serves `max-age=86400` from the manifests' own `ttl_sec`
rather than the endpoint's 300, and a fault injection confirms `max-age` follows
a changed `ttl_sec`. See [`RESOLUTION.md`](RESOLUTION.md).

**The normative text this endpoint implements.** `webjson/PROVENANCE` pins the
tag the *generated artifacts* come from, now `v1.8` (`4b5652c`, the stamp), and
0.7.0 moved the whole package there at once: artifacts, IDL, and the native
layer's bindings and registry. Everything vendored is byte-identical at `v1.8`,
`v1.8.1` and `v1.8.2`, which moved only the conformance runner. Text and
artifacts have moved on different commits before (`169fabd` amended Appendix N
without regenerating anything, and `9c3e1f2` regenerated without amending the
appendix), so the pins are still stated separately.

From `9c3e1f2` to `v1.8` the vendored set changed in five places:

- **`PixFormat.GRAY8`**: one appended enumerator, reaching three vision schemas
  and the serializer tables.
- **A comment block in `types.idl`**: it changes no type, but it does change
  the flattened bytes every recording embeds, so the fountain recording was
  re-authored. Its messages are byte-identical.
- **`MAPPING.md`**: it gained the lift direction, the three non-identity rules
  run backwards.
- **A BlobChunk golden vector**: the first non-empty octet run. It found that
  `gates/golden_json.py` had never handled an octet run in a vector's `sample`
  (finding 59).
- **One normative paragraph in N.3**: a four-segment topic is a resource
  rooted in the well-known namespace. Until 0.7.0 this gateway dropped such
  topics, and the spec's own suite measured it (finding 60).
  `gates/short_topics.py` now serves one and checks it like a scene-scoped
  collection.

The revision added two members this repository had asked for — `297c433` gave
`GeoAnchor` a `manifest_uri` (1.9 candidates entry 2) and `9c3e1f2` gave
`TopicMeta` a `durability` (entry 3, closing finding 17). Both are APPENDABLE
and wire-compatible. The re-vendor changed four artifacts: those two schemas,
`Announce` (which embeds `TopicMeta`), and the serializer tables; `MAPPING.md`
also grew an **Open questions** section about the literal rule making every
member `required`, which this gateway demonstrated by failing
`validates against its published schema` while one revision behind. Four new
golden vectors arrived with it, two per member, covering present and absent.

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

**Resolution is certified upstream.** Findings 21 and 22 were dispatched and are
closed by SpatialDDS-spec `c8743ef`, which gave the suite a resolver check and a
`--connect` option for an endpoint whose advertised origin differs from the
address it is reached at. Both shapes now run, and both pass:

Re-run at 0.7.0 with the suite from SpatialDDS-spec `v1.8.2`, which added the
streamed-payload and four-segment probes. Every request is kept on loopback by
`--connect`:

| shape | run | result |
|---|---|---|
| serving the fountain, as the deploy does | `--endpoint https://demo.spatialdds.org --connect http://127.0.0.1:PORT` | **15 checks, 15 passed, 0 failed, 0 skipped** |
| the same, publish surface **on** | plus `--publish` on the endpoint | **15 checks, 15 passed, 0 failed, 0 skipped** |
| **a native participant's session**, recorded by its tap (`p.record()`) and served with `--mcap` | `--endpoint https://registry.example --connect …` | **15 checks, 14 passed, 0 failed, 1 skipped** (§8.2: no anchors in the session) |
| serving the retail recording | the same connect form | 15 checks, 13 passed, **1 failed**, 1 skipped |
| an empty registry | `python -m spatialdds.serve --authority … --publish` | 15 checks, 6 passed, 0 failed, 9 skipped |

**The retail row's failure is the recording's, and it is new only because the
check is.** The v1.8.2 suite validates every streamed payload, and the vendored
retail recording's `Detection2D` carries no `det_id`, a member 1.8 requires.
Its embedded IDL predates the 1.8 types. The 0.6.0 gateway gives the identical
failure under the same suite, so this is not 0.7.0's doing (finding 65).

**The empty registry now reports honestly.** It used to be "5 failed" under
the old suite. The suite reports its data-dependent checks as skipped now, as
finding 39 asked, so the empty registry is 6 passed and 9 skipped.

That last row is new in 0.2.0 and it is the suite's gap rather than this
endpoint's. An empty registry is now the normal way to start one: it serves a
conformant surface with nothing in it, and five of the suite's checks are
data-dependent and report *failure* where the suite's own UNEXERCISED
vocabulary is the right answer. One of them says so in as many words: "no
latched resource to read". Filed as finding 39 for the specification queue;
nothing about the normative text changes, only the runner's reporting. The
front page's conformance line is therefore scoped to shapes that serve data.

**§8.2 is exercised for the first time** in the first two rows: *1
anchor-referenced manifest(s) resolved, 1 ref(s) fetchable*. It had skipped
since it was written, because no 1.8 anchor type could carry the reference the
scenario needs; the revision landed the member and the fountain recording's
plaque anchor names the label's content manifest. The third row still reports
it UNEXERCISED, correctly: the retail recording has no anchors, so there is
nothing for the scenario to follow, and that is an absence rather than a
defect.

The third row is the point of the write surface being implementation-defined:
the suite's result is identical with it enabled, because it never touches those
routes and a read-only client cannot tell a published record from a replayed
one. See [`PUBLISH.md`](PUBLISH.md).

The resolver check is fully exercised only in the second: *resolver 200
(authority demo.spatialdds.org); 2 resolved, 1 announced-but-not-held (404); ttl
2 doc-governed / 0 class-default; error branches 400/400/404*. Against the
non-authority shape it reports the resolve surface UNEXERCISED rather than
passing on nothing, which is the vocabulary working as intended. The one
remaining skip is §8.2, which waits on `GeoAnchor.manifest_uri`.

[sc]: https://github.com/OpenArCloud/spatialdds-scenescape
