# Changelog

## 0.7.4: what the first extension found

Found by SpatialDDS-demo's OWM 0.2 prototype, the first deployment
extension served over the binding and recorded, and by its adoption on
0.7.3 (WEB-BINDING-FINDINGS.md, findings 71 and 72). Additive, except where
a served index grows: an endpoint serving an extension now lists it.

- **Fixed: an extension was served with no schema** (S19). Appendix N.2:
  "An endpoint MUST publish a schema for every type it serves." The
  gateway now writes one for each served type the vendored set lacks, from
  the model it decodes the type with (`webjson.schema_from_model`), and
  `/.well-known/spatialdds/schemas` and `/schemas/{type}` serve it. The
  writer follows the specification's generator and is held to it: from the
  pin's model it reproduces all 112 vendored schemas exactly. The IDL model
  now resolves a sequence bound given by name (`SZ_SMALL`).
- **Fixed: `serve --mcap` served an extension lane volatile and
  operator-scoped** (S20). The recording tap writes each channel's
  durability, and the loader serves a type with no row in the gateway's
  table latched when its writer opened it TRANSIENT_LOCAL. Who may read it
  stays the deployment's call: **`serve --public FQN`**, or
  `build_state(public=)`.
- **`withdraw(key, *, reason, **changes)`** (S18) sets the type's other
  members on the terminal sample, such as a validity interval closed at
  retirement, so an instance ends in one terminal sample, not two. The
  members it sets itself and the key are refused.
- **Fixed: `close()` raised when a reader did not acknowledge in time**
  (S17). cyclonedds-python 11.0.1's `wait_for_acks` raises AttributeError
  on its timeout; that error, and only that one, is now the timeout, so a
  clean stop with a slow peer finishes its flushes and its recordings.
- **Fixed: `sd.stamp` could emit `nanosec` = 10^9, and misread a time before
  the epoch** (S16). It is one count of nanoseconds, floored.
- `sd.dds.extensions()` and `sd.dds.extension_of()` are exported, and
  `extend()`'s refusal of a bare include names the rooted spelling.

## 0.7.3: what the first migration found

Found by SpatialDDS-demo's migration onto 0.7.2 (WEB-BINDING-FINDINGS.md,
finding 70).

- **Fixed: frames had no identity** (S14). `earth_fixed()` had an empty
  uuid, and so did every announce built here, `frame()`'s default, the
  manifest builder's coverage and the gateway's own manifest. Appendix G:
  "Implementations MUST treat `uuid` uniqueness as the identity key." Each
  now carries the uuid its fqn derives to (`frames.frame_uuid`, UUIDv5 of
  the fqn). **This changes bytes**: the earth-fixed frame's uuid is
  `00cc62ae-c21e-5d3b-b5f9-2587a084ba31` where it was `""`, and ETags over
  such documents change with it. `frame(uuid=)` still takes a token of your
  own. The packaged demo set's two content manifests are re-authored.
- **Fixed: §2's lane templates were refused** (S15). §2 gives
  PlannedTrajectory `spatialdds/<scene>/plan/<agent_id>/trajectory/v1`, and
  §3.3.1 lets such templates have extra segments. The registry now reads
  §2's template sentences from the spec, and `check_topic`, and so
  `topic=`, admit a name that fills one. A five-segment name is still
  admitted for these types, since §2 says SHOULD. The gateway still serves
  four- and five-segment topics only: N.3 gives a longer template no path.
- **`p.announce(topics=)` takes readers** (S11), each described by its own
  `TopicMeta`, so a service can announce the lane it reads requests on.
  `auth_hint=` and `coverage_frame_ref=` pass through. Readers gain
  `topic_meta()`, `type_name` and `profile`.
- **`from_json(..., strict=True)`** (S12) refuses every member the type does
  not have, at any depth, by name, as `SchemaInvalid`. The default stays
  the gateway's tolerant lift.
- **`WireError` is a `ValueError`**, exported as `sd.dds.WireError` (S13).

## 0.7.2: a deployment's own types

Additive. **`sd.dds.extend(type_name, cls, idl)`** registers a deployment's
own type (a documented §3.3.2 extension) with the native layer. Before it,
such a type could be written only with `validate=False`, and nothing else
worked: it had no N.1 conversion, no key, no `class_for`, and no recorded
schema (finding 69, found by SpatialDDS-demo's migration). After it, the
type converts through the gateway's codec like any pinned type, keys on its
own `@key`, is named by `class_for`, is recorded with its IDL, and is opened
by its name. With no published schema, `validate()` checks N.1
representability. The error for an unregistered type now names `extend()`.

## 0.7.1: for programs that own their loop

Additive. Every change is something SpatialDDS-demo's adoption, the native
layer's first real consumer, found missing (WEB-BINDING-FINDINGS.md, finding
67). Nothing 0.7.0 did changes.

- **`Reader.take()`** (S1): the non-blocking drain, with exactly one
  `EndOfReplay`, on the first take at which the replay is complete. That is
  decided before the call drains, so no historical sample lands behind the
  marker. `events()` is `take()` in a loop.
- **`topic=`** on `writer()` and `reader()` (S2): the whole name, held to
  §3.3.1 by `registry.check_topic`. A well-known topic is refused (ask for it
  by message name), and a templated type is held to its template.
  `records.topic(name=)` announces such a lane truthfully.
  **`reader(history=)`** overrides the reader's own cache.
- **The announce** (S3):
  - `transforms=` carries the service's frame placement;
  - `service_id=` and `topics=` announce several services from one
    participant, each with its own writer, TTL and departure;
  - **`p.relay()`** re-publishes other services' announces with its own
    writer, marked `relayed_by`. It refuses its own services and chained
    relays, and unregisters, never disposes, on close.
- **`reader(ignore_local=True)`** (S4), **`matched()` / `wait_matched()`** on
  writers and readers (S5), and **`sd.dds.class_for(name)`** (S9).

Fixed: an `announce()` that failed on its first publish made `close()` raise
`AttributeError`, which hid the failure.

`gates/native_additions.py` holds every addition against a bus, and each of
its 16 plants fails it. Spec queue entry 10 (`Announce` has no member saying
who relayed it) is filed from S3's design.

## 0.7.0: a native participant, the v1.8 tag

### The native layer (`[dds]`)

`pip install "spatialdds[dds]"` (cyclonedds `>=11.0.1,<12`, mcap, jsonschema),
then `sd.participant(...)` beside `sd.connect(...)`. It is the library four
codebases each hand-rolled (docs/NATIVE-SDK-CENSUS.md), built once:

- **Refuses what it will not guess:**
  - no default scene;
  - no silent domain 0 (`domain=` or `SPATIALDDS_DDS_DOMAIN`);
  - no invented authority;
  - no QoS profile where §3.3.2 names none;
  - no latched keyed writer without `on_exit="keep"` or `"withdraw"`.

  Every refusal from one call is raised together.
- **A crash is never a dispose.** Writers never autodispose (not a
  parameter), so a killed participant reaches readers as
  `Liveliness(alive=False)`.
- **A removal says why.** `withdraw(key, reason=)` writes the terminal sample
  and then the dispose. The reason goes in the type's own member when it has
  one, and always in the recording and the log.
- **A clean stop departs.** The announce sends a terminal sample, its dispose,
  then `Depart`, and acknowledgements are awaited.
- **The announce is generated.** It is built from the writers you opened and
  refreshed every `ttl_sec / 3`. `floor_hz` keeps a stationary publisher
  fresh, and is refused below its profile's deadline.
- **The same events as the web client.** `p.reader(...).events()` yields
  `Sample`, `Dispose`, `EndOfReplay` and `Liveliness`. `Sample.data` is the
  typed sample.
- **One JSON dialect.** `to_json` and `from_json` are the gateway's codec run
  on native samples, so a native sample and a served one cannot differ.
- **Everything is checked before it is written:** each sample against its
  published schema, and against N.1's finite-only rule.
- **A recording tap.** `p.record(path)` writes typed MCAP that
  `spatialdds-serve --mcap` serves, disposes and all.
- **Bindings for every type** in the pinned IDL (`spatialdds.dds.types`),
  generated by `tools/generate_types.py` and gated against drift.
- **The registry and QoS table** are generated from the spec text at the pin.
- **Blobs.** `sd.dds.chunk` and `Reassembler` check each chunk's CRC32 and the
  whole blob's SHA-256.
- **Without the extra,** `sd.participant` raises `sd.MissingExtra` naming it.

Pure Python, needing nothing:

- `sd.frames`, one copy of the ENU↔ECEF arithmetic;
- `sd.Recency`, learned per-key idle thresholds;
- the new builders `sd.entity` and `sd.model_pose`.

Request/reply is documented as a convention (docs/REQUEST-REPLY.md), not
built.

### One pin: the `v1.8` tag

The schemas, serializer tables, IDL (now the whole normative tree plus
`provisional/`), golden vectors and MAPPING.md move from `9c3e1f2` to `v1.8`
(`4b5652c`). `SPEC_TAG` joins `SPEC_COMMIT`. The delta is `PixFormat.GRAY8`,
comments, the lift section of MAPPING.md, a BlobChunk vector and one N.3
paragraph.

### The gateway

- **Four-segment topics are served** at
  `/.well-known/spatialdds/<stream>/<type>/<version>`, read-only, where they
  were dropped (finding 60). A well-known announce belongs to the scene its
  `manifest_uri` names (finding 66).
- **A recording's dispose metadata is applied**, so a withdrawal is served with
  its reason.
- **Schema closures are computed** from the IDL's includes, for any type.
- **A recording holding a NaN or an infinity is refused by name.**
  `spatialdds-serve` exits with the topic, the instance key and the member,
  where it used to die on an unnamed `ValueError`, or, for a streamed sample,
  send `Infinity` on the wire, which is not JSON (finding 64).

### Changed, and may need a line of yours

- `sd.topic()` derives `durability` from a registered `qos_profile` and
  refuses a contradicting one (finding 62). A templated type gets its
  template's name.
- The event classes live in `spatialdds.events`, and are re-exported where
  they were.

### Found and filed

Findings 59–66 are in WEB-BINDING-FINDINGS.md. Among them, the vendored
retail recording predates 1.8's `det_id` (65); its recapture or retirement is
a follow-up of its own. Spec-queue entry 9
holds what the census found the spec does not say.

The repository is renamed **spatialdds-py**, and the package name is
unchanged.

## 0.6.0 — lanes, hardening, OpenAPI

### The token-table model (multi-tenancy)

One registry, several publishers. **A scene is a lane.** The scene is the first
segment of every resource path and the second of every `spatialdds://` URI the
registry holds. A bearer token resolves to a *principal*:

- a name;
- a provider identity, recorded on every journal entry it writes;
- the scenes it may write;
- whether it may create scenes;
- an optional record quota.

- `python -m spatialdds.serve --tokens FILE` serves a token table. The file
  holds the SHA-256 of each token, never the token itself. `SIGHUP`
  (`systemctl reload`) re-reads it without dropping a connection, and a file
  that fails to load is refused while the old table keeps serving. `--token TOK`
  still means one principal over every scene.
- `python -m spatialdds.tokens` issues tokens (printing each once), lists them
  and revokes them; `--max-records` sets a quota.
- How writes are refused:

  | Request | Answer |
  | --- | --- |
  | no token, or one nobody issued | `401` |
  | a token outside its lane | `403 not-your-lane`, naming the scene |
  | a scene that exists nowhere, without `may_create_scenes` | `403 scene-creation-not-granted` |
  | a new record past the quota | `403 quota-exceeded` |

  Reads stay open. A scene exists when it is served or when a grant names it,
  so an empty lane needs no create right.
- Manifests can be withdrawn: `DELETE /spatialdds/resolve?uri=…`, checked
  against the same lane rule. A withdrawal is now whole.
- Every journal entry carries its principal and scene, and replays with them.
  `python -m spatialdds.journal reset-scene` resets one lane offline and leaves
  every other lane byte-identical.
- Virtual authorities (one DNS name per partner) are designed, not built. The
  hook is `tenancy.AUTHORITIES`.

### Security fixes

- **The client sent its bearer token to asset hosts** (finding 45). An
  `Endpoint` with a token now sends it only to the origin it was connected to.
- **The banner printed a pinned token** into the system log (finding 42). It
  now prints a token only when it generated one.
- **A NUL in an asset name was a `500`**, and the error logged a traceback. A key
  or asset name containing a control character is now refused with `400`.
- **A full disk was a `500`, and left the registry disagreeing with its
  journal.** Writes now journal first, and an unwritable journal is
  `503 journal-unwritable`. Reads are unaffected, and recovery needs nothing
  but free space.
- **A long request target was refused as plain text**, with a traceback, by the
  HTTP parser. It is now a `414` problem document.
- **Bodies were accepted under any content type.** Anything but
  `application/json` is now `415`.
- **The router's own `404` and `405` were plain text.** They are now problem
  documents.
- **Token checks compare every row**, whichever row matches.

### OpenAPI

- `/.well-known/spatialdds/openapi` serves the REST surface as OpenAPI 3.1, and
  `openapi.html` beside it is a static reference page. The document is
  generated from the gateway's own tables, the published JSON Schemas and the
  write rule, and a gate fails if it drifts.
- The WebSocket streams are described in prose, not modelled.
- A Python client generated by openapi-generator 7.10.0 runs the read flow.

### Limits

Each refusal is a problem document naming its limit and number.

| limit | default | refusal |
| --- | --- | --- |
| request body | 256 KiB (`--max-body`) | `413` |
| request target | 16 KiB | `414` |
| concurrent WebSocket subscriptions | 256 (`--max-ws`) | `503` |
| requests per client IP | 20/s, burst 60 (`--rate`) | `429`, with `Retry-After` |
| records per scene, per principal | the token table's `max_records` | `403` |

The client IP is the rightmost `X-Forwarded-For` entry from a loopback proxy,
and the peer otherwise. The local operator is not rate-limited.

### Changed, and may need a line of yours

- **The client's publish verbs refuse when no scene is set** (finding 46).
  They used to write into `default`. Pass `scene=` to `connect()` or to the
  call; reading is unaffected.
- **The demo seed's manifest URIs name the scene**:
  `spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/…`, where they
  used to say `…/south-mall/…`. A URI saved from 0.5.x will not resolve.
- **A manifest is served in its canonical form from the moment it is
  accepted** (finding 41), so its bytes no longer change at the first restart.

Earlier releases are described in the package README.
