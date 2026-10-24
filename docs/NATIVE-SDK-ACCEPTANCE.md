# The native SDK: acceptance, run and quoted

The brief's five acceptance items (`native-sdk-brief.md` §4), each with the run
that answers it. Every block below is a program's own output, copied from the
run, not retyped. The gates named here are in `gates/run_all.py` and run on
every gate run. The conformance runs, the clean-venv runs and the fit check are
recorded here because they need a spec checkout, PyPI or a scratch copy of
SpatialDDS-demo.

Built at the `v1.8` tag (`4b5652c`). cyclonedds 11.0.1. Each DDS run uses a
domain of its own (150-229), clear of any other participant on this host.

## 1. A participant in a dozen lines on a bare scene

An entity published with an announce, pose updates, and a clean withdrawal on
exit. This is the README's block (15 non-blank lines), run from a clean
venv where `spatialdds[dds]` 0.7.0 was installed from its built wheel, in
isolated mode from a directory with no checkout:

```python
import spatialdds as sd

with sd.participant("bare-scene", domain=42, service_id="svc:duck",
                    authority="registry.example") as p:
    frame = sd.frame("map/bare-scene", convention="ENU", meters_per_unit=1.0)
    duck = p.writer("model_entity", qos_profile="MAP_META", on_exit="withdraw")
    pose = p.writer("model_pose", qos_profile=sd.dds.Profile(
        "DUCK_POSE", reliability="BEST_EFFORT", durability="VOLATILE"))
    p.announce("OTHER", coverage=sd.circle(30.2849, -97.7341, 50))
    duck.write(sd.entity("ent:duck", frame_ref=frame, source_id="svc:duck"))
    for i in p.loop(hz=5):
        pose.write(sd.model_pose("ent:duck", position=(i * 0.1, 0, 0),
                                 source_id="svc:duck"))
        if i == 9:
            break
print("stopped; the duck was withdrawn on the way out")
```

It printed:

```console
stopped; the duck was withdrawn on the way out
```

A reader on the same domain, the README's other block, printed:

```console
EndOfReplay
Sample ent:duck ACTIVE
Sample ent:duck RETIRED
Dispose ent:duck 'the participant stopped'
```

`gates/native_readme.py` runs both blocks on every gate run and requires each to
print exactly its fence.

## 2. Crash semantics, shown failable

`gates/native_lifecycle.py`. Each case is a separate process, an SDK
participant. The control arm is the same participant with autodispose left at
DDS's default. It is the arm that shows the SIGKILL case can fail:

```
  ok   SIGKILL: the reader had the entity first
  ok   SIGKILL: the entity reaches the reader as Liveliness(alive=False) (10.0 s, the lease)
  ok   SIGKILL: and never as Dispose, entity or announce
  ok   SIGKILL: the announce, too, is liveliness and not removal
  ok   control arm: the plant took (autodispose at the DDS default)
  ok   control arm: the same kill with the DDS default IS a Dispose (10.1 s), so the case above can fail
  ok   clean stop, withdraw: the child closed cleanly
  ok   clean stop, withdraw: a RETIRED terminal sample, immediately followed by its Dispose
  ok   …the Dispose carries the reason, and so does the terminal sample's own state_reason
  ok   clean stop: the announce departs, its Dispose then a Depart for the service
  ok   clean stop, keep: the entity is not disposed
  ok   …its writer leaving is Liveliness(alive=False), promptly (a clean unregister, not a lease)
  ok   …and the announce departs all the same
  ok   floor: one write, kept fresh at 4 Hz: 10 samples in about 3 s, stamps strictly rising
  ok   floor control: no floor, one write, seen 1 time(s)
PASS, a crash is liveliness, a clean stop withdraws what it said, and the floor keeps a still publisher fresh
```

The lease the SIGKILL case waits out is cyclonedds's default of ten seconds.
The clean-stop `keep` case is prompt because `close()` unregisters what it
keeps. A first draft deleted nothing, and the instances ended only at the lease
(10.2 s, measured). That is finding 63.

## 3. The round trip through the published ecosystem

`gates/native_roundtrip.py`: an SDK session recorded by its tap, served by the
gateway (`gateway.build_state`, as `spatialdds-serve --mcap` does), and every
payload validated with counts:

```
  ok   the tap recorded the session: {'samples': 12, 'disposes': 3}
  ok   every one of the 12 recorded payloads validates against its published schema (Announce 2, ContentAnnounce 2, Depart 1, Entity 2, ModelPose 5)
  ok   every topic is served where N.3 puts it, the four-segment discovery lanes in the well-known namespace
  ok   nothing in the recording went unnamed
  ok   the withdrawn entity, the withdrawn item and the departed announce are not in their latched sets
  ok   the entity's stream ends in its terminal sample (RETIRED) then a dispose carrying its reason
  ok   …and its first sample is exactly what the native layer's to_json said it wrote
  ok   the content item, a type with no member for a reason, is withdrawn on the stream with the reason its recording metadata carries
  ok   …and the item it served is exactly the native to_json of what was written
  ok   the Depart lane is served, a stream rather than a latched set
PASS, a native session, recorded and served, is what the participant wrote
```

**Conformance, with SpatialDDS-spec's suite at `v1.8.2`**, extracted from the
tag. The target is `spatialdds-serve --mcap` on a recorded session of the
README's participant, with two entities, one withdrawn, and its announce in
force. Every request was kept on loopback:

```
python3 web-binding/conformance/run.py --endpoint https://registry.example \
    --connect http://127.0.0.1:PORT --token …
```

```
  PASS     schemas endpoint is self-describing
  PASS     every resource validates against its published schema  (1 latched instance(s) across 1 lane(s); UNEXERCISED latched (empty collection — streamed data checked over N.5): ['spatialdds/bare-scene/world/model_pose/v1'])
  PASS     every streamed payload validates against its published schema  (13 streamed payload(s) across 2 lane(s))
  PASS     canonical form on the wire  (int64_string=1, enum_name=5, guard_literal=4, union_tagged=0; UNEXERCISED by this endpoint's data: ['union_tagged'])
  PASS     latched state is GET with ETag, Cache-Control and 304  (1 latched resource(s))
  PASS     access classes, both directions  (0 public, 2 operator-scoped; UNEXERCISED: public-cacheable (no public resource to test the cacheable path))
  PASS     DDS-join material optional-and-validated  (bootstrap=404, connection=absent, resolver=404)
  PASS     resolver is a checked surface (§7.5.2)  (resolver 404 — the endpoint is not an authority (169fabd); the resolve surface is UNEXERCISED (zero observations))
  PASS     service manifests match §8.2.3  (1 manifest(s))
  PASS     commands are POST, or cleanly absent  (no lane advertises accepts_commands; POST -> 405 (refuse branch, conformant))
  PASS     the carried QoS subset is observable, the rest absent  (durability ['TRANSIENT_LOCAL', 'VOLATILE'] — both kinds carried)
  PASS     §8.1 hello spatial in ten lines  (10 lines, 1 replayed sample(s), marker then live)
  SKIPPED  §8.2 anchor resolve flow
  PASS     §8.3 backend-blind, the same suite for both shapes  (no backend branch; runner inputs ['connect', 'endpoint', 'token'])
  PASS     §8.4 third-party reader, published schemas only  (1 instance(s) read from the published schemas alone)
15 checks: 14 passed, 0 failed, 1 skipped (skipped: §8.2 anchor resolve flow)
```

The skip is §8.2's anchor-resolve flow, correctly unexercised: the session has
no anchors. For comparison, the packaged fountain with the same suite:

```
  PASS     schemas endpoint is self-describing
  PASS     every resource validates against its published schema  (8 latched instance(s) across 3 lane(s))
  PASS     every streamed payload validates against its published schema  (8 streamed payload(s) across 3 lane(s))
  PASS     canonical form on the wire  (int64_string=15, enum_name=17, guard_literal=19, union_tagged=10)
  PASS     latched state is GET with ETag, Cache-Control and 304  (3 latched resource(s))
  PASS     access classes, both directions  (3 public, 0 operator-scoped; UNEXERCISED: operator-scoped (no gated resource to test the 401/bearer path))
  PASS     DDS-join material optional-and-validated  (bootstrap=404, connection=absent, resolver=present)
  PASS     resolver is a checked surface (§7.5.2)  (resolver 200 (authority demo.spatialdds.org); 2 resolved, 1 announced-but-not-held(404); ttl 2 doc-governed / 0 class-default (one TTL leg UNEXERCISED: no resolved manifest omits ttl_sec); error branches 400/400/404)
  PASS     service manifests match §8.2.3  (1 manifest(s))
  PASS     commands are POST, or cleanly absent  (1 advertised command lane(s), body synthesized from published schema: ['geo_anchor=202 accepted']; selected by accepts_commands, not by position)
  PASS     the carried QoS subset is observable, the rest absent  (durability [TRANSIENT_LOCAL]; UNEXERCISED: the endpoint is uniformly TRANSIENT_LOCAL, so the VOLATILE/TRANSIENT_LOCAL distinction has no counter-example to observe)
  PASS     §8.1 hello spatial in ten lines  (10 lines, 2 replayed sample(s), marker then live)
  PASS     §8.2 anchor resolve flow  (1 anchor-referenced manifest(s) resolved, 1 ref(s) fetchable)
  PASS     §8.3 backend-blind, the same suite for both shapes  (no backend branch; runner inputs ['connect', 'endpoint', 'token'])
  PASS     §8.4 third-party reader, published schemas only  (8 instance(s) read from the published schemas alone)
15 checks: 15 passed, 0 failed, 0 skipped
```

## 4. The fit check, without touching the demo repository

The census maps every `spatialdds_demo` helper a demo component uses to its SDK
equivalent: 143 symbols, generated from an AST walk of the demo's tracked files
(`docs/NATIVE-SDK-CENSUS.md`, appendix).

One small demo publisher, `spatialdds_demo/pond_watch.py`, was rebuilt on the
SDK in a scratch copy of SpatialDDS-demo at `0434a8b`, taken with `git archive`.
The demo repository was not written to and has no worktree for this. Nothing
was committed there.

| | lines | code lines | import statements |
| --- | --- | --- | --- |
| the prototype | 179 | 107 | 17 |
| on the SDK | 127 | 60 | 7 |

`diff`: 29 insertions, 81 deletions. Code lines exclude comments and
docstrings, counted with `tokenize`. The docstring, the constants and the
observation logic are unchanged. The transport, the record construction and
the exit are what moved. The exit becomes `on_exit="withdraw"`, which fixes
the defect filed against the demo (a dispose with no terminal sample).

**Proof of fit.** The ported publisher ran with `--seed 7`. The demo's own
reader read it, unchanged code: `typed_transport.make_reader` on
`TOPIC_MODEL_ENTITY_V1` with `MODEL_LATCHED`. The publisher was then sent
SIGTERM. The reader recorded:

```
valid=True instance_state=16 state=ACTIVE state_reason=''
valid=True instance_state=16 state=ACTIVE state_reason=''
valid=True instance_state=32 state=RETIRED state_reason='the participant stopped'
```

The first entity it received equals the original `observation(Random(7))` in
every member but `stamp`. The last is the terminal sample, with the dispose
carried on it as instance state 32.

## 5. The README section

The package README now opens its body with "A native participant, over DDS",
beside the web quickstart. It carries the two blocks above, what the layer
requires and refuses, what it does unasked, and the pointers to `sd.frames`,
`sd.Recency` and the request/reply convention. Both blocks are gated
(`gates/native_readme.py`), and the page's other rules hold:
`gates/library.py` requires every python fence on it to have an instrument
behind it, every link absolute, and every `__all__` name present.

## And the install shapes

From the same built wheel, in clean venvs, run in isolated mode from a directory
with no checkout:

- **`pip install spatialdds`** brings no cyclonedds. `sd.participant` raises
  `MissingExtra: the native layer needs cyclonedds: pip install
  "spatialdds[dds]"`, while `sd.connect`, `sd.frames` and the registry work.
- **`pip install "spatialdds[dds]"`** brings cyclonedds 11.0.1, mcap and
  jsonschema. The README blocks print their fences, and the tap and `to_json`
  find their IDL inside the wheel. That is the condition spatialdds_ros's
  recorder fails under (its finding 1).
