# The native SDK: a four-repo census, and the API it proposes

Four programs speak SpatialDDS natively over DDS, and each one has written the
same library for itself:

- `spatialdds_demo`, the prototype;
- `spatialdds_ros`;
- the SceneScape sidecar;
- openvps's DDS face.

This census inventories what each one implements, gives every subject a
verdict, and ends with the public API surface proposed for the `[dds]` layer of
this package. **It stops there for a ruling.** Nothing in `lib/` has changed.

| verdict | meaning |
| --- | --- |
| **SDK** | one implementation, in `spatialdds` under the `[dds]` extra (or pure Python beside it) |
| **LOCAL** | stays in the repository that owns it |
| **CONVENTION** | written down, not built: the shape is settled, but one library would be premature |

**Sources, read-only, at these heads:**

| repo | head | DDS face |
| --- | --- | --- |
| SpatialDDS-demo | `0434a8b` | `spatialdds_demo/`, 6,744 lines |
| spatialdds-ros | `f36958b` (0.4.0, prepared) | `spatialdds_ros/`, 3,146 lines |
| spatialdds-scenescape | `c26d7a2` | `sidecar/`, 2,597 lines |
| openvps | `8ab4d24` | `maplocalizer/server/spatialdds/`, about 1,085 lines, plus a whole-package vendored copy of `spatialdds_demo` at `6f29ecf` |

**How it was gathered:**

- I read `spatialdds_demo` directly. The other three were inventoried by
  independent read-only passes against a fixed subject list.
- Each claim below that carries weight was then re-checked at its source
  line; the defects marked *measured* were reproduced rather than read.
- The demo-helper fit map (appendix) comes from an AST walk over the demo's
  git-tracked files, not from a hand list. An untracked 899 MB CDK build tree
  under `deploy/aws/` had inflated an earlier, untracked-inclusive count 30×.
- The mapper refuses any symbol it has no verdict for. That refusal was
  planted (one module's default deleted) and fired.

## The headline

- **Four spec pins, two IDL byte-sets.**
  - The demo, ROS and this package pin `9c3e1f2`.
  - openvps pins `5ee5f96`, a later commit whose `idl/v1.8` tree is
    byte-identical to `9c3e1f2`'s.
  - The SceneScape sidecar pins `424a8b3` (2026-10-03). That is **12 days
    behind**, so its `GeoAnchor` lacks `manifest_uri` and its `TopicMeta` lacks
    `durability`.
  - Since then the spec has been stamped: `v1.8` registers `model_pose` and
    `model_command`, and renames the entity topic from `model/entity/v0` to
    `world/entity/v1`. Nothing in the ecosystem pins `v1.8`.
- **Zero QoS tables are generated from the spec.**
  - The demo hand-transcribes all of §3.3.3, and openvps vendors the demo's
    1.7-era copy.
  - ROS hand-writes its three lanes.
  - SceneScape hand-writes two ad hoc profiles twice, once each for the bridge
    and the watcher. Neither matches the registry for the types it carries.
- **One of the four writers sets autodispose off.**
  - That is ROS, measured on 2026-10-21. In the other three, a crashed
    participant's latched instances reach readers as `NOT_ALIVE_DISPOSED`, a
    deliberate removal, about ten seconds after the crash. Re-measured today
    against the demo (below).
  - §2.14 says liveliness loss is not removal, so three of the four violate it
    on every crash.
- **Zero of the four implement §2.14's removal on a general type** (a terminal
  sample with a reason, then dispose).
  - The demo's `ModelPublisher.retire` does it for world-model entities.
  - SceneScape writes the terminal sample and never disposes.
  - `pond_watch` and `ModelPublisher.close` dispose with no terminal sample.
  - The `ContentAnnounce` and `Announce` paths everywhere dispose with no
    terminal sample, because those types have no member to carry one (spec
    queue, below).
- **Four independent WGS84 implementations**:
  - demo `plaza.enu_to_ecef_pose`;
  - ROS `frames.py`, the most complete;
  - SceneScape `mapping.py`, plus a second copy in `gates/golden_point.py`;
  - openvps `oscp/geopose_utils.py`.

  They use three different ECEF→geodetic inversions: ROS iterates 8 times,
  SceneScape iterates to convergence, and openvps uses single-step Bowring.
- **Two JSON dialects.**
  - This package's `canonical`/`webjson` is N.1: a union is
    `{"type": "<ENUMERATOR>", …}`.
  - The demo's `json_mapping`, which openvps vendors, writes
    `{"discriminator": …}`, cyclonedds's spelling.
  - The demo's mapper accepts this package's builder output for `Announce` and
    `ContentAnnounce` byte-for-byte after `lift`, but **refuses
    `GeoAnchor`**: `'type' is not one of its cases`.
- **Topic names are hand-written f-strings in all four.** This package's
  `records.topic()` is the only function that builds one from its segments.

## Measured today

**A demo-built writer that dies, seen from a demo-built reader.** The probe is
`ContentAnnouncePublisher` and `ContentInventory` from `spatialdds_demo`
unchanged, one process each on an isolated domain. The reader starts, the
writer publishes one item, then the writer is `kill -9`ed at about 7 s.
Instance states: 16 is ALIVE, 32 is NOT_ALIVE_DISPOSED and 64 is
NOT_ALIVE_NO_WRITERS.

```
# as shipped (DDS default autodispose)
reader: 2.1 valid 16
reader: 2.1 inventory ['content/probe']
reader: 12.2 invalid 32
reader: 12.2 inventory []

# control arm: the same writer with WriterDataLifecycle(autodispose=False)
writer: autodispose off
reader: 2.1 valid 16
reader: 2.1 inventory ['content/probe']
reader: 12.2 invalid 64
reader: 12.2 inventory []
```

This shows two independent defects.

1. **The writer side.** With the default, a crash arrives as a dispose.
2. **The reader side.** With the writer fixed, the crash arrives as no-writers,
   and `ContentInventory.refresh` *still purges the item*, because it treats
   every invalid sample alike (`discovery_bus.py:230-232`).
   `AnnounceSubscriber.poll` does the same for services
   (`discovery_bus.py:160-163`).

The control arm is what shows the probe can tell the two states apart: the
"autodispose off" line confirms the plant took. In the SDK build, both arms
become failability tests.

**The ROS recorder from the installed wheel.** `spatialdds_ros` 0.3.0, the
release the demo pins, resolves
`recording.IDL_DIR = …/site-packages/idl/v1.8`, and `exists()` is `False`.
`idl/` is in no `packages`, `package_data` or `data_files` (`setup.py:50-59`),
so `Recorder` works only from a source checkout.

## Subjects, one verdict each

### 1. Types, vendoring, pin, drift gate → **SDK**

| | demo | ROS | SceneScape | openvps |
| --- | --- | --- | --- | --- |
| generator | `scripts/generate_types.py` (idlc + post-processing) | none: vendors the demo's tree | `tools/generate_bindings.py`, "a copy of" the demo's, with a TODO to unify | none: vendors the demo's tree |
| pin recorded | docstring + `test_topic_registry` | `PROVENANCE.md`, `SPEC_COMMIT` | `idl/PROVENANCE`, never read | `__init__.py` comment |
| drift gate | `--check` regenerates and diffs, and `test_idl_trees_agree` holds it equal to ROS | none | none | none |

The SDK takes the demo's generator, the copy SceneScape already took, and makes
it the only one: `tools/generate_types.py` here, `--check` as a gate, at this
package's own `SPEC_COMMIT`. **One package, one pin**: the native layer cannot
pin differently from the web artifacts beside it, or it would be two type
systems in one wheel. The bindings ship nested under the package (layout in the
API section). Until consumers adopt it, an agreement gate holds the SDK's tree
equal to the demo's `spatialdds_idl18` modulo the package prefix.

### 2. Participant, writers, readers, QoS → **SDK**

| | demo | ROS | SceneScape | openvps |
| --- | --- | --- | --- | --- |
| QoS source | hand table of all §3.3.3 + 5 local profiles (`qos_profiles.py`) | hand table, 3 lanes (`qos.py:93-115`) | two hand copies (`bridge.py:52-57`, `watch.py:56-61`) | vendored demo 1.7 table |
| unknown profile | raises | raises | n/a | raises |
| profile for an unassigned type | **silently `EVENT_RT`** (`topic_types.profile_for`) | n/a | n/a | n/a |
| Topic QoS | default (`Topic(dp, name, T)`) | default, deliberately | default | default |

The §3.3.3 table is normative ("writers and readers SHALL set exactly these
policies"), so the SDK generates it from the spec text at the pin. A drift gate
compares the generated table to the spec's.

The type→profile map is §3.3.2's own column. Two rows name no profile:
`navsat_status` and `entity_binding`. For those the SDK refuses rather than
defaulting. The demo had invented `POSE_RT` and `MAP_META` for them, and
defaults anything else to `EVENT_RT`. Both go to the spec queue.

### 3. Topic names → **SDK**

| | demo | ROS | SceneScape | openvps |
| --- | --- | --- | --- | --- |
| how | module constants and f-strings (`topics.py`) | `Topics(scene)` with hardcoded group, type and version | one f-string function per type (`sidecar/topics.py:19-79`) | literals, written three times |
| registry gate | `test_topic_registry`: names against the spec at the pin, with `KNOWN_DIVERGENCE` | none (grammar only) | none; `geo_anchor` and `frame_transform` are not registry types | the 1.7 registry, wrong era |

The SDK has one function, `topic_name(scene, stream, type, version)`, shared
with `records.topic()` so the web and native layers build the same string. One
registry gate over that one function covers every caller.

### 4. Writer lifecycle → **SDK**, defaults below

| | demo | ROS | SceneScape | openvps |
| --- | --- | --- | --- | --- |
| autodispose | default TRUE (measured above) | **off** (`qos.py:86`) | default (its watcher logs 317 disposes per camera on stop) | default |
| deliberate removal | `ModelPublisher.retire`: RETIRED + reason, settle, dispose | none: silence is published as `UNOBSERVED` with a reason | `retire_entity` writes RETIRED + reason, **never disposes** | `clear()` disposes the announce |
| announce TTL refresh | n/a (lifespan only) | every `ttl/3` | no announce at all | every `ttl/2` |
| clean exit | SIGINT/SIGTERM flag, then `close()` disposes everything, with no terminal sample | standalone: dispose the announce, keep the entity; ROS node: no SIGTERM, no announce | flag checked only between stdin lines | FastAPI lifespan, then `depart()`; flush unverified |
| floor-rate republish | none | 2 Hz pose floor, config refuses a floor ≤ 1 Hz | none | none |

The defaults the SDK takes:

- autodispose off, always;
- withdrawal per §2.14;
- announce refreshed at `ttl/3`;
- the departing announce disposed and followed by `Depart` (§3.3);
- `wait_for_acks` before the participant is deleted, so a clean exit is
  flushed rather than hoped;
- the floor opt-in, refused if it cannot meet the profile's deadline.

**What a clean exit does to a latched entity is the open question.** It is
ruling 2.

### 5. Latched reading → **SDK**

| | demo | ROS | SceneScape | openvps |
| --- | --- | --- | --- | --- |
| instance state | `take_with_state` → `TypedSample(alive, instance_state)` | `TakenSample`, validity apart from aliveness | none: `take(N=10)` | vendored, unused |
| end of replay | none | none | none | none |
| learned recency | `tempo.Tempo` | the same `Tempo`, copied | none | none |

The web client already has the vocabulary: `Sample`, `Dispose`,
`EndOfReplay` and `Liveliness`, in `client.py:42-70`. The native reader emits
those same classes. `EndOfReplay` is
`DataReader.wait_for_historical_data` (cyclonedds 11.0.1). What that silence
means is in the API section.

### 6. Liveliness: crash is never a dispose → **SDK**

ROS is right on both sides, and is the model:

- its writers set autodispose off;
- its readers keep the record on `NOT_ALIVE_NO_WRITERS`
  (`awareness.py:185-191`);
- a fresh sample un-flags it.

The demo is wrong on both sides (measured). SceneScape prints every invalid
sample as "disposed / unregistered". openvps distinguishes nothing.

### 7. Frame and anchor math → **SDK**, pure Python, outside the extra

ROS's `frames.py` becomes the one copy: `enu_to_ecef_pose`, `geodetic_of`,
`compose_point` (returning `(None, reason)`, never a default) and
`frame_problem` (refusing non-metric frames, per §2.13's conversion rule).

- **Its docstring contradicts its code** on the direction of `T_parent_child`
  (`frames.py:9-10` against `:110-111`). That is filed, and the SDK copy
  states the direction once.
- **The demo's `plaza.enu_to_ecef_pose` must agree with it numerically.** A
  gate compares them at the venue anchor.
- **SceneScape's similarity fit and anisotropy measure stay local.** They are
  georeferencing from camera corners, not frame arithmetic.

### 8. Payload builders, JSON mapping → **SDK**, unified on N.1

This package's `records` builders already exist and produce N.1 documents. The
native writer accepts those documents (lifted) or typed samples. Its mapper is
the demo's `json_mapping`, kept for its strictness (an unknown union case is
refused), but switched to N.1's union key and gated against `webjson` both ways.

The demo's FrameRef builders (`payloads.frame_ref`, `content_catalogue._frame`,
`spatialdds_validation.create_frame_ref`) become `records.frame()`. Fusion,
detection and SceneScape translation payloads stay local.

### 9. Blobs and the recording tap → **SDK**

**Chunking.** The demo's `blob.chunk` and `Reassembler` (per-chunk CRC32),
which openvps vendors. The SDK adds what openvps lacks: the whole-blob SHA-256
`BlobRef.checksum` is verified on reassembly by default (`service.py:234` reads
only `blob_id` and `role`).

**The recording tap.** There are three recorders today:

- the demo's JSON recorder;
- ROS's `Recorder`, CDR plus omgidl through this package's `pin.flatten`;
- SceneScape's `McapOut`, the same idea with a hand module table that gives
  an unknown module an empty schema.

The SDK takes ROS's shape, aligned with the gateway journal. Disposes are
written as `spatialdds-web:dispose` metadata carrying the reason, so
`spatialdds-serve --mcap` replays a withdrawal. Schema closures are computed
from the IDL includes for every registered type, replacing the three-entry
hand map in `pin.CLOSURES`.

### 10. Validation hooks → **SDK**

Today only the demo validates on the publish path: `validate_announce`, with
the registry check, refusing at the writer. Everyone else validates in tests
or not at all. SceneScape's `schema_check` checks embedded schemas load, not
payloads.

The SDK validates every written sample against this package's published JSON
Schema for its type (`jsonschema`, in the extra), on by default. An announce is
also checked against the registry at the pin.

### 11. The rest

| subject | where | verdict |
| --- | --- | --- |
| VPS request/reply, `query_id` correlation, `;v=` revisions, chunks parked before their request | demo `service_bus`, openvps `service.py` | **CONVENTION** for 0.7.0. Two implementations with different needs; the building blocks are SDK |
| coverage query/response (`keep_all` reader for many responders) | demo `service_bus` | **CONVENTION** |
| one-writer guard (`already_published`, 2.5 s settle) | ROS | **LOCAL**; candidate later |
| config refusals collected and reported together | ROS `config.py:186-287` | the SDK's constructor discipline follows it |
| `oarc.*` extension types | demo | **LOCAL** |

## Filings, by repository

These defects are recorded here for one review, and filed into each repo's own
findings with the build batch. openvps is read-only, so its filings are for
James to forward.

**SpatialDDS-demo**

1. Writers keep the default autodispose, so a crash is a dispose. *Measured.*
2. `ContentInventory` and `AnnounceSubscriber` purge on no-writers, against
   §2.14 ("MUST NOT purge … on liveliness loss alone"). *Measured.*
3. `pond_watch` and `ModelPublisher.close` dispose without a terminal sample
   (§2.14).
4. `topic_types.profile_for` silently defaults unassigned types to `EVENT_RT`,
   and assigns invented profiles to `navsat_status` and `entity_binding`.
5. `require_dds_env` defaults the domain to `"0"` when it is unset.
6. `json_mapping`'s union dialect is not N.1. Its web surface is unaffected,
   because the bridge projects through this package's `webjson`. MQTT is
   unverified.

**spatialdds-ros**

1. The ROS node never announces, and its FrameTransform lane is missing from
   the standalone announce.
2. The recorder is unusable from a wheel. *Measured on 0.3.0.* It also imports
   `spatialdds.server`, which is not in `install_requires`.
3. The `frames.py` docstring has the direction backwards.
4. The bindings' docstring says 1.7.
5. `already_published` may count a dead writer's record as live. Unverified
   on the wire.
6. The ROS install path has no Python cyclonedds dependency.
7. SIGTERM is unhandled in the ROS node.
8. `cyclonedds>=0.10` against a tested 11.0.1.

**spatialdds-scenescape**

1. `watch.py` crashes at startup with `TYPES['owm_entity']` KeyError.
   Verified statically; no gate runs the watcher.
2. Default autodispose: stopping the sidecar withdraws every zone and anchor.
3. `retire_entity` never disposes.
4. QoS contradicts the registry for `detection2d`, `fused_track` and
   `spatial_event`, in two copies.
5. `geo_anchor` and `frame_transform` are not registry type segments.
6. The pin is recorded and never checked, and is 12 days behind the
   ecosystem's.
7. `Detection2DSet` instances grow without unregistering.
8. Events with no `scene_id` get `spatialdds//events/…`.
9. The signal flag is only seen between stdin lines.
10. Stale prose.

**spatialdds-web (this repo)**

1. `records.topic()` defaults `durability="TRANSIENT_LOCAL"` whatever
   `qos_profile` says (`records.py:442-460`). A `POSE_RT` row built with it
   claims a durability its profile contradicts. The demo derives the value from
   the profile instead (`qos_profiles.durability_for`), and the SDK has to do
   the same, because one builder serves both layers.

**openvps**, for James to forward

1. HTTP localization bypasses the "one shared worker".
2. The DDS poll blocks for GPU time.
3. The whole-blob SHA-256 is never verified.
4. `confidence=1.0` is fabricated (`main.py:165`).
5. A 1.8 announce is validated against the 1.7 registry.
6. The `GEOM_TILE` chunk reader requests TRANSIENT_LOCAL, so a volatile client
   writer silently never matches.
7. No autodispose or liveliness handling.
8. Stale vendor README and dangling links.

## Spec queue

These go to `directions/spatialdds-1.9-candidates.md` with origins.

1. **§2.14 cannot be met by types with no terminal-state member.** The removal
   rule covers "all RELIABLE + TRANSIENT_LOCAL keyed types", but the following
   carry no member for a terminal state or a reason:
   - `Announce`
   - `ContentAnnounce`
   - `GeoAnchor`
   - `SpatialZone`
   - `CrossingLine`

   And §3.3's departure rule (dispose + `Depart`, no terminal sample) is a
   second, conflicting removal rule for `Announce`. The existing
   typed-removal-record direction answers the first half; the precedence
   between §3.3 and §2.14 is new.
2. **Autodispose is unstated.** §3.3.3 lets "other DDS policies" be tuned, and
   §2.14 forbids reading liveliness loss as removal. But DDS's default
   (`autodispose_unregistered_instances = TRUE`) turns liveliness loss *into* a
   dispose on the wire before any consumer sees it. Candidate: writers of
   latched keyed types SHALL set it FALSE. Evidence: the probe above, and ROS's
   2026-10-21 measurement.
3. **Registry completeness.** `GeoAnchor` (served as `frames/geo_anchor/v1`),
   `FrameTransform` (`frames/frame_transform/v1`) and `ContentAnnounce` (a
   well-known topic) are published as topics and have no §3.3.2 row, which the
   completeness rule calls a spec defect. `navsat_status` and `entity_binding`
   have rows with no QoS profile.
4. **What a native reader's "caught up" means.** N.5's `end_of_replay` has no
   DDS counterpart in the spec. The SDK's definition (below) is a convention
   until the spec says something.

## The proposed API surface: the stop

### Shape

```python
import spatialdds as sd

with sd.participant("ut-austin-littlefield", domain=7,
                    service_id="svc:pondwatch",
                    authority="demo.spatialdds.org") as p:
    pond = p.writer("model_entity", on_exit="keep")      # topic, type, QoS: the pin's registry
    p.announce("OTHER", coverage=sd.circle(30.2849, -97.7341, 60))
    for _ in p.loop(hz=0.2):                             # ticks; ends on SIGINT/SIGTERM
        pond.write(observation())                        # N.1 dict or typed sample, validated
# exit: on_exit per writer; announce disposed + Depart; acks awaited
```

```python
with sd.participant("ut-austin-littlefield", domain=7) as p:   # read-only: no service_id
    for ev in p.reader("model_entity").events():
        match ev:
            case sd.Sample(key=k, data=d):     ...   # d is N.1, as the gateway serves it
            case sd.Dispose(key=k, reason=r):  ...   # someone chose it
            case sd.Liveliness(alive=False):   ...   # writer gone; record kept
            case sd.EndOfReplay():             ...   # caught up with the writers matched so far
```

### Constructors: what each one requires, and what silence means

**`sd.participant(scene, *, domain=None, service_id=None, authority=None)`**

| argument | discipline | when absent |
| --- | --- | --- |
| `scene` | required; segment grammar `^[A-Za-z0-9][A-Za-z0-9._-]*$` | refuse to start. No `default` scene (finding 46's rule, carried over) |
| `domain` | required, or `SPATIALDDS_DDS_DOMAIN` | refuse. DDS's 0 is a choice nobody made, and the demo's silent `"0"` is filed |
| `service_id` | required to write or announce | a read-only participant; `writer()` and `announce()` refuse |
| `authority` | required to announce, unless `manifest_uri=` is given | `announce()` refuses; a `spatialdds://` authority is never invented (`records._uri`'s rule) |
| `CYCLONEDDS_URI` | convention: the environment's | the middleware's default, untouched |

All refusals are collected and raised together, ROS's pattern.

**`p.writer(type_name, *, stream=None, qos_profile=None, on_exit=…, floor_hz=None, validate=True)`**

| argument | discipline | when absent |
| --- | --- | --- |
| `type_name` | registered at the pin, or an extension passed as a class | refuse an unknown name |
| `stream` | documented default where the registry row names the topic (`model_*` → `world`) | otherwise refuse: the stream is the deployment's choice |
| `qos_profile` | documented default: §3.3.2's profile for the type | refuse where the row names none (`navsat_status`, `entity_binding`, extensions). Never `EVENT_RT` by default |
| autodispose | not a parameter | always off |
| `on_exit` | **required** for latched keyed types: `"keep"` or `"withdraw"` | refuse. Both answers are claims, and the library cannot know which is true (ruling 2) |
| `floor_hz` | optional; republishes each key's last sample with a fresh stamp | no floor. Refused below the profile's deadline rate, rather than offering a contract the writer will not meet |
| `validate` | on: every sample against the published schema | — |

**`w.withdraw(key, *, reason)`**: `reason` is required and must not be empty.

- On a type with a lifecycle member (`owm::Entity`): a terminal sample with
  `RETIRED` and `state_reason`, then dispose (§2.14).
- On a type without one: dispose, with the reason written to the recording
  tap. This is documented and spec-queued (entry 1), and never presented as
  §2.14-complete.

**`p.announce(kind, *, coverage, ttl_sec=300, …)`**

- Builds through `records.service_announce`, so it is one builder for both
  layers.
- `topics` is the writers already opened; it is never written by hand.
- Checked against the registry at the pin.
- Lifespan is `ttl_sec`, refreshed every `ttl_sec/3` by `tick()`.

**`p.reader(type_name, *, stream=None, qos_profile=None)`** has the same
resolution as `writer()`.

| event | when | when silent |
| --- | --- | --- |
| `Sample` | an alive valid sample | — |
| `Dispose` | `NOT_ALIVE_DISPOSED` only | its `reason` comes from the preceding terminal sample where the type has one, else `""` |
| `Liveliness(alive=False)` | `NOT_ALIVE_NO_WRITERS`; **never a `Dispose`**, and nothing is purged | `alive=True` on the next sample |
| `EndOfReplay` | once, after `wait_for_historical_data` | "complete for the writers matched when it fired". A latched writer discovered later is more `Sample`s after it. VOLATILE lanes emit it at once, with `replayed=0` |

Keys are the web binding's instance names, from the same `@key` model the
gateway uses, so a native key and a gateway path name the same instance.

**`p.tick()` and `p.loop(hz)`**: no hidden threads. `tick()` refreshes the
announce and runs the floors. `loop()` is a generator that ticks, sleeps, and
installs SIGINT/SIGTERM handlers only while it is iterating (restored after).
A participant stays a plain program.

**Pure Python, no extra needed:**

- `sd.frames`: ROS's `frames.py`;
- `sd.Recency`: `Tempo`;
- `sd.dds.topic_name`;
- the QoS and registry tables, as data.

### Dependency shape

- **Base (unchanged):** `requests`, `websockets`. `import spatialdds` never
  imports cyclonedds; `sd.participant` resolves lazily.
- **`[dds]`:** `cyclonedds>=11.0.1,<12` (the only version any of the four has
  tested), plus `mcap` and `jsonschema`.
- **Without the extra:** `sd.participant(...)` raises `sd.MissingExtra`, an
  `ImportError` subclass:

  ```
  the native layer needs cyclonedds: pip install "spatialdds[dds]"
  ```

  This is gated in a venv with no cyclonedds.
- **Layout:**
  - `spatialdds/dds/` is the layer;
  - `spatialdds/dds/_types/` holds the generated bindings, nested so they
    cannot collide with a consumer's own `spatialdds_idl18` before it adopts.
    Wire typenames come from the IDL scope, so nesting changes nothing on the
    wire.
  - `tools/generate_types.py` is the generator, in Docker with idlc, plus a
    `--check` gate.

## Rulings requested

1. **The pin.** The SDK must share the package's pin, and the choice is between:
   - **(a)** stay at `9c3e1f2`, the ecosystem's, and move to `v1.8` in its own
     brief;
   - **(b)** move the whole package to the `v1.8` tag in 0.7.0: the
     artifacts, schemas and `SPEC_COMMIT`. The IDL delta is two comment
     blocks and one appended enumerator (`PixFormat.GRAY8`). The registry
     delta is what matters: `model_pose` and `model_command` rows, and the
     entity topic at `world/entity/v1`, which is what the demo and ROS
     already publish.

   I recommend **(b)**. At `9c3e1f2` the SDK's registry would name
   `model/entity/v0`, and refuse `model_pose` and `model_command` outright,
   for the very lanes acceptance 1 publishes.
2. **Clean exit and latched instances.** The brief says "a clean stop must
   withdraw". ROS's measured design withdraws the *announce* and keeps the
   *entity*, because a robot still exists when its bridge stops; the demo
   disposes everything. I propose:
   - the announce always departs (§3.3's MUST);
   - each latched keyed writer must say `on_exit="keep"` or `"withdraw"`;
   - acceptance 2 asserts both choices;
   - the alternative is a default of `"withdraw"`.
3. **Types without a terminal member.** Should `withdraw()` on `ContentAnnounce`,
   `GeoAnchor` and the like be a dispose with the reason recorded only in the
   tap, as proposed, or refused until the spec gives them a terminal member?
4. **Request/reply as CONVENTION for 0.7.0**, the VPS and coverage flows. These
   are documented, and the building blocks are SDK.
5. **`Sample.data` is the N.1 dict**, identical to what the gateway serves for
   the same instance, with the typed sample on `.typed`. Or is the typed
   sample the default?

## Appendix: every demo helper a demo component uses

Generated from the demo's git-tracked files at `0434a8b`, by an AST walk of
`from spatialdds_demo… import` and `module.attr` uses. The consumers are 74 tracked files: every file outside `spatialdds_demo/` and
the top-level `tests/`, plus the four programs inside the package
(`model_service`, `duck_mover`, `robot_bridge`, `pond_watch`). Of the 74, 52 are
programs and modules, and 22 are test files that live beside the components
they test; the column counts both. The verdict mapper refuses a symbol it
cannot place.

**143 symbols: SDK 71, CONVENTION 10, LOCAL 60, RETIRES 2.**

| symbol | components | verdict | SDK equivalent |
| --- | --- | --- | --- |
| `absent.ABSENT_SCALE` | 2 | SDK | absent-value constants beside the types |
| `absent.absent_detection3d` | 1 | SDK | absent-value constants beside the types |
| `blob.BLOB_PROFILE` | 2 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.BLOB_TOPIC` | 4 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.BLOB_TYPE` | 4 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.BlobPublisher` | 1 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.BlobSubscriber` | 1 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.CorruptChunk` | 2 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.Reassembler` | 5 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.blob_ref` | 4 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.checksum` | 1 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `blob.chunk` | 3 | SDK | `p.blob_writer()` / `sd.dds.Reassembler` |
| `content_catalogue.authority` | 1 | LOCAL | the demo's catalogue |
| `content_catalogue.content_announce` | 3 | LOCAL | the demo's catalogue |
| `content_catalogue.content_manifest` | 1 | LOCAL | the demo's catalogue |
| `content_catalogue.filter_inventory` | 4 | LOCAL | the demo's catalogue |
| `content_catalogue.load_seed` | 3 | LOCAL | the demo's catalogue |
| `content_catalogue.resolve` | 1 | LOCAL | the demo's catalogue |
| `content_catalogue.resolver_metadata` | 1 | LOCAL | the demo's catalogue |
| `content_catalogue.zone` | 1 | LOCAL | the demo's catalogue |
| `dds_transport.DDSTransport` | 1 | RETIRES | envelope baseline; benchmarks only |
| `dds_transport.require_dds_env` | 14 | SDK | `sd.participant(domain=)` or `SPATIALDDS_DDS_DOMAIN`; no silent 0 |
| `discovery_bus.AnnouncePublisher` | 2 | SDK | `p.announce()` / `p.reader(sd.dds.ANNOUNCE)` |
| `discovery_bus.AnnounceSubscriber` | 2 | SDK | `p.announce()` / `p.reader(sd.dds.ANNOUNCE)` |
| `discovery_bus.ContentAnnouncePublisher` | 2 | SDK | `p.announce()` / `p.reader(sd.dds.ANNOUNCE)` |
| `discovery_bus.ContentInventory` | 3 | SDK | `p.announce()` / `p.reader(sd.dds.ANNOUNCE)` |
| `discovery_bus.validate_announce` | 1 | SDK | `p.announce()` / `p.reader(sd.dds.ANNOUNCE)` |
| `discovery_http.BOOTSTRAP_SCHEMA` | 2 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.DiscoveryError` | 3 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.ServiceRecord` | 2 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.bootstrap_manifest` | 2 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.coverage_intersects` | 1 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.geohash_bounds` | 1 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.normalize_search_request` | 1 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.query_from_geohash` | 1 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.record_from_announce` | 5 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.record_from_manifest` | 1 | LOCAL | HTTP discovery is the web package's `connect()` |
| `discovery_http.search` | 4 | LOCAL | HTTP discovery is the web package's `connect()` |
| `duck_mover.AVOID_ENTITY_ID` | 1 | LOCAL | the demo's mover |
| `duck_mover.AVOID_RADIUS_M` | 1 | LOCAL | the demo's mover |
| `duck_mover.AVOID_STALE_S` | 1 | LOCAL | the demo's mover |
| `duck_mover.DEFAULT_BOUNDS_ENTITY` | 1 | LOCAL | the demo's mover |
| `duck_mover.DuckMover` | 1 | LOCAL | the demo's mover |
| `duck_mover.INSET_M` | 1 | LOCAL | the demo's mover |
| `duck_mover.farthest_corner` | 1 | LOCAL | the demo's mover |
| `json_mapping.FIELD_ALIASES` | 2 | SDK | `sd.dds.to_json` / `from_json`, N.1 dialect |
| `json_mapping._resolved_hints` | 1 | SDK | private today; the SDK's mapper owns it |
| `json_mapping._typename` | 1 | SDK | private today; the SDK's mapper owns it |
| `json_mapping._unwrap` | 1 | SDK | private today; the SDK's mapper owns it |
| `json_mapping.from_json` | 23 | SDK | `sd.dds.to_json` / `from_json`, N.1 dialect |
| `json_mapping.to_json` | 17 | SDK | `sd.dds.to_json` / `from_json`, N.1 dialect |
| `keepout.FREE` | 1 | LOCAL | robot-tier planning |
| `keepout.build_mask` | 2 | LOCAL | robot-tier planning |
| `keepout.contributing` | 1 | LOCAL | robot-tier planning |
| `keepout.describe` | 1 | LOCAL | robot-tier planning |
| `manifest_resolver.resolve_manifest` | 3 | SDK | `sd.connect(...).resolve()` (exists) |
| `model_service.ModelPublisher` | 1 | LOCAL | the demo's world model |
| `model_service.POND_MAX` | 3 | LOCAL | the demo's world model |
| `model_service.POND_MIN` | 3 | LOCAL | the demo's world model |
| `model_service.SHALLOWS_MAX` | 1 | LOCAL | the demo's world model |
| `model_service.SHALLOWS_MIN` | 1 | LOCAL | the demo's world model |
| `model_service.TYPE_BASIN` | 1 | LOCAL | the demo's world model |
| `model_service.TYPE_RUBBER_DUCK` | 1 | LOCAL | the demo's world model |
| `model_service.VENUE_FRAME_FQN` | 2 | LOCAL | the demo's world model |
| `model_service.live_log` | 3 | LOCAL | log throttling |
| `model_service.seed_entities` | 2 | LOCAL | the demo's world model |
| `model_service.venue_frame` | 2 | LOCAL | built with `sd.frame()` (exists) |
| `payloads.ABSENT_CROSSING` | 1 | SDK | `spatialdds.records` builders (N.1 form) |
| `payloads.ABSENT_SCALE` | 4 | SDK | `spatialdds.records` builders (N.1 form) |
| `payloads.COV_NONE` | 3 | SDK | `spatialdds.records` builders (N.1 form) |
| `payloads.SCHEMA_CORE` | 2 | SDK | schemas served by the package (exist) |
| `payloads.SCHEMA_DISCOVERY` | 1 | SDK | schemas served by the package (exist) |
| `payloads.SCHEMA_EVENTS` | 1 | SDK | schemas served by the package (exist) |
| `payloads.SCHEMA_SEMANTICS` | 2 | SDK | schemas served by the package (exist) |
| `payloads.absent_fused_track_observer` | 1 | SDK | `spatialdds.records` builders (N.1 form) |
| `payloads.detection3d` | 3 | LOCAL | fusion-demo payload shape |
| `payloads.detection_set` | 3 | LOCAL | fusion-demo payload shape |
| `payloads.frame_ref` | 5 | SDK | `spatialdds.records` builders (N.1 form) |
| `payloads.stamp` | 1 | SDK | `spatialdds.records` builders (N.1 form) |
| `payloads.vec` | 1 | SDK | `spatialdds.records` builders (N.1 form) |
| `plaza.EARTH_FIXED_FQN` | 1 | SDK | `sd.earth_fixed()` (exists) |
| `plaza.GROUND_Z` | 4 | LOCAL | venue constants stay in the demo |
| `plaza.ROBOT_START_XY` | 3 | LOCAL | venue constants stay in the demo |
| `plaza.VENUE_ANCHOR_HEIGHT_M` | 1 | LOCAL | venue constants stay in the demo |
| `plaza.VENUE_ANCHOR_LAT_DEG` | 1 | LOCAL | venue constants stay in the demo |
| `plaza.VENUE_ANCHOR_LON_DEG` | 1 | LOCAL | venue constants stay in the demo |
| `plaza.enu_to_ecef_pose` | 2 | SDK | `sd.frames.enu_to_ecef_pose` |
| `qos_profiles.MODEL_COMMAND` | 8 | SDK | registry default for `model_command` |
| `qos_profiles.MODEL_FAST` | 5 | SDK | registry default for `model_pose` |
| `qos_profiles.MODEL_LATCHED` | 11 | SDK | registry default for `model_entity` (MAP_META values) |
| `qos_profiles.get` | 3 | SDK | `sd.dds.qos` (generated from §3.3.3 at the pin) |
| `service_bus.BootstrapClient` | 1 | CONVENTION | request/reply documented, not built in 0.7.0 |
| `service_bus.BootstrapService` | 1 | CONVENTION | request/reply documented, not built in 0.7.0 |
| `service_bus.CoverageClient` | 4 | CONVENTION | request/reply documented, not built in 0.7.0 |
| `service_bus.CoverageService` | 2 | CONVENTION | request/reply documented, not built in 0.7.0 |
| `service_bus.VpsClient` | 3 | CONVENTION | request/reply documented, not built in 0.7.0 |
| `service_bus.VpsService` | 2 | CONVENTION | request/reply documented, not built in 0.7.0 |
| `stream.StreamPublisher` | 4 | SDK | `p.announce()` opens no writers; `p.writer()` declares lanes |
| `stream.StreamSubscriber` | 7 | SDK | `p.announce()` opens no writers; `p.writer()` declares lanes |
| `tempo.GAP_SMOOTHING` | 1 | SDK | `sd.dds.Recency` |
| `tempo.IDLE_FACTOR` | 1 | SDK | `sd.dds.Recency` |
| `tempo.IDLE_FLOOR_S` | 1 | SDK | `sd.dds.Recency` |
| `tempo.LATCH_EVERY_N` | 1 | SDK | `sd.dds.Recency` |
| `tempo.Tempo` | 2 | SDK | `sd.dds.Recency` |
| `topic_types.ALL` | 2 | SDK | `sd.dds.types` registry at the pin |
| `topic_types.DEFAULT_PROFILE` | 1 | SDK | `sd.dds.types` registry at the pin |
| `topic_types.PROFILE_FOR_TYPE` | 1 | SDK | `sd.dds.types` registry at the pin |
| `topic_types.WELL_KNOWN` | 2 | SDK | `sd.dds.types` registry at the pin |
| `topic_types.profile_for` | 1 | SDK | `sd.dds.types` registry at the pin |
| `topic_types.resolve` | 15 | SDK | `sd.dds.types` registry at the pin |
| `topic_types.try_resolve` | 8 | SDK | `sd.dds.types` registry at the pin |
| `topics.SCENE` | 4 | LOCAL | the demo's scene name; passed to `sd.participant(scene=)` |
| `topics.TOPIC_ANCHORS_DELTA` | 2 | SDK | `sd.dds.topic_name(scene, stream, type)` |
| `topics.TOPIC_BOOTSTRAP_QUERY_V1` | 3 | LOCAL | `oarc.bootstrap_*` extension lanes |
| `topics.TOPIC_BOOTSTRAP_RESPONSE_V1` | 3 | LOCAL | `oarc.bootstrap_*` extension lanes |
| `topics.TOPIC_DDS_ENVELOPE_V1` | 1 | RETIRES | envelope baseline |
| `topics.TOPIC_DISCOVERY_ANNOUNCE_V1` | 5 | SDK | `sd.dds.ANNOUNCE` |
| `topics.TOPIC_DISCOVERY_CONTENT_V1` | 5 | SDK | `sd.dds.CONTENT` |
| `topics.TOPIC_DISCOVERY_QUERY_V1` | 7 | CONVENTION | well-known name, constant in `sd.dds`; coverage query documented |
| `topics.TOPIC_DISCOVERY_RESPONSE` | 3 | CONVENTION | coverage query documented |
| `topics.TOPIC_FRAME_TRANSFORM_V1` | 2 | SDK | `sd.dds.topic_name(scene, stream, type)` |
| `topics.TOPIC_MODEL_COMMAND_V1` | 8 | SDK | `sd.dds.topic_name(scene, stream, type)` |
| `topics.TOPIC_MODEL_ENTITY_V1` | 11 | SDK | `sd.dds.topic_name(scene, stream, type)` |
| `topics.TOPIC_MODEL_POSE_V1` | 5 | SDK | `sd.dds.topic_name(scene, stream, type)` |
| `topics.TOPIC_SOURCE_ANNOUNCE_PREVIEW` | 4 | LOCAL | web-bridge provenance labels |
| `topics.TOPIC_SOURCE_FALLBACK` | 3 | LOCAL | web-bridge provenance labels |
| `topics.TOPIC_SOURCE_MANIFEST` | 3 | LOCAL | web-bridge provenance labels |
| `topics.TOPIC_SOURCE_REQUEST` | 5 | LOCAL | web-bridge provenance labels |
| `topics.TOPIC_SOURCE_SPEC` | 5 | LOCAL | web-bridge provenance labels |
| `topics.TOPIC_VPS_QUERY_V1` | 6 | CONVENTION | well-known name, constant in `sd.dds`; request/reply documented |
| `topics.TOPIC_VPS_RESULT_V1` | 5 | CONVENTION | well-known name, constant in `sd.dds`; request/reply documented |
| `topics.topic_meta` | 2 | SDK | `sd.topic()` (exists; must derive durability from the profile, filed) |
| `topics.validate_topic_meta` | 2 | SDK | registry check inside `p.announce()`, at the pin |
| `topics.validate_topics_are_canonical` | 2 | SDK | folded into `sd.dds.topic_name()`: built, never checked after |
| `typed_transport.TypedDictWriter` | 12 | SDK | `p.writer()` / `p.reader()` |
| `typed_transport.make_reader` | 15 | SDK | `p.writer()` / `p.reader()` |
| `typed_transport.make_writer` | 13 | SDK | `p.writer()` / `p.reader()` |
| `typed_transport.take_samples` | 12 | SDK | `p.writer()` / `p.reader()` |
| `typed_transport.take_with_state` | 5 | SDK | `p.writer()` / `p.reader()` |
| `spatialdds_demo_idl18.oarc_demo` | 1 | LOCAL | demo-owned `oarc.*` extension types |
| `spatialdds_demo_idl18.oarc_demo.BootstrapQuery` | 1 | LOCAL | demo-owned `oarc.*` extension types |
| `spatialdds_demo_idl18.oarc_demo.BootstrapResponse` | 1 | LOCAL | demo-owned `oarc.*` extension types |
| `spatialdds_demo_idl18.oarc_demo.FusionCoverage` | 3 | LOCAL | demo-owned `oarc.*` extension types |
