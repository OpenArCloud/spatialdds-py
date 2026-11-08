# The web binding: scope, promises, and the path to a normative write surface

Design note, Nov 2026. Pre-normative. Feeds the 1.9 register entry on the normative write surface.

## 1. Why this note

The web binding began as the quick way to let a browser read a bus, and its read half earned its keep: GET with ETag and 304, a WebSocket for subscriptions, recordings and replay. The write half grew by need, one endpoint at a time, and recent findings show what growing by need leaves behind: a validated write path hundreds of times slower than the unvalidated hot lane next to it (finding 75), an error body that leaked a journal path (78), a schema index that lists a type only once something has written to it (73, 79), and, when we went looking for the contract these should be judged against, the discovery that the spec's web binding appendix defines no write surface at all. The recent discussions kept drifting into implementation details because there is no design to point at instead. This note is that design, at the level a spec register entry needs: what the binding is, what it promises, and what 1.9 should make normative.

## 2. What the binding is

A web binding server is a hosting role, not a component of the model. It serves one or more scenes over HTTP and WebSocket. Behind it there may be a DDS domain, or nothing but the server's own store: OWM does not require DDS transport, it requires a carrier that keeps four obligations, and the binding is one such carrier.

The four carrier obligations, which any transport serving OWM must keep:

1. Keyed last-value: one current record per instance, new value replaces old.
2. A freshness signal: a reader can tell how stale a record is, and observed records expire unless refreshed.
3. Ordered revision: revisions of one keyed record arrive in order; history is on the id.
4. Grounded assertion: the asserter on a record is established by the transport or the credential, never typed into the payload.

Everything else in this note is these four obligations translated into HTTP terms.

The unit the binding serves is the scene: a world's live public state. A server may host many scenes; a scene lives on one server at a time for writes. Anyone can run a binding server. The one structural rule carried over from deployment practice, now promoted to design: one gateway owns each web-writable lane. This started as a cache-coherence necessity and turns out to be the policy enforcement point as well; a write that violates a scene's policy is refused at the single place all writes pass through.

## 3. The tempo boundary

The binding carries a world's public state surface at world-model tempo, not its game loop. Entities, claims, declared futures, retirements: the statements other parties act on, at the rates those statements change. The recorded crossing session, around 270 messages per second sustained, is the tempo class in evidence. Render loops, control loops, raw sensor streams and anything per-frame stay behind the asserter, exactly as camera frames stay behind the SceneScape sidecar. Heavy bytes travel off-bus by manifest reference, never inline. A request for the binding to carry a game loop is a request for a different product, and the answer is no in the design rather than a throttle in the implementation.

## 4. The read contract

Already de facto normative; written here so the write contract has a mirror.

- GET a scene, a lane, or a keyed record by its URL. Responses carry ETag; conditional requests get 304. Reads are open by default and cacheable; a CDN in front of a binding server must not change any reader-visible semantics.
- Subscribe over WebSocket to a lane or scene and receive the current value per instance, then changes in revision order.
- Every record arrives with its freshness: stamp, validity, and for observed records the refresh contract it is under. A reader can always distinguish "current" from "lingering".
- Replay of a recording is served through the same read surface as live state. A reader should not need different code for a live scene and a recorded one.

## 5. The write contract

The 1.9 candidate. A writer is any credentialed participant publishing through the binding rather than through DDS: a roadside sensor with a SIM, a world publishing its presence, a city tool issuing policy records.

1. **Writes are keyed records.** PUT places or revises the record for one instance on one lane. Command-style lanes (the geo_anchor pattern) take POST and are requests, not state; the distinction is part of the surface, not a convention.
2. **Every write is validated, and validation cost is bounded.** The current split, a validated slow path beside an unvalidated hot lane, is the absence of a contract. The contract: all writes are validated against the lane's type, and the implementation owes a performance budget (schema caching is the known fix, scheduled with a perf gate). An unvalidated write path is not a fast mode, it is a hole.
3. **Refusals are explicit, typed, and clean.** A refused write names the rule it broke (schema, credential scope, policy) in a stable machine-readable form, and the error body never exposes server internals. A refused write changes nothing: it does not open a lane, create an instance, or alter any index (the finding 73/79 interaction, stated as a rule).
4. **The writer inherits liveliness.** An observed record published over HTTP is under the same refresh-or-expire obligation as one published over DDS. The binding provides the mechanism: a declared refresh interval per writer or per record, expiry when it lapses, and retirement only as an explicit act with a reason. A dropped connection is a crash, and a crash is never a retirement.
5. **The asserter is the credential.** The binding stamps the asserter from the presented token. A payload that names a different asserter is refused. This is obligation 4, and it is what makes the credential system part of provenance rather than just access control.
6. **Writes are ordered per instance.** The binding serializes revisions of one keyed record; a stale revision (older than the current one by the writer's own sequence) is refused, not silently reordered.

## 6. Credentials

Thin in the spec, boring in the implementation.

- The spec defines bearer-token semantics and a scope grammar, nothing more. A scope is a lane-and-asserter grant: permission to write a named lane or lane pattern as a named asserter. Read scopes exist for the rare non-public scene.
- The recommended issuance profile is OAuth 2 client credentials: machine to machine, short-lived access tokens, refresh at the issuer, revocation at the issuer. Proof-of-possession (DPoP) is the extension point if token theft and asserter impersonation become a live threat.
- Issuance is per scene operator. There is no central authority and no registry of writers; a scene's operator decides who writes, the same way a site decides who posts.
- Today issuance is a person sending tokens. The flow above replaces the person, not the judgment.

## 7. Names

- The canonical public name of a scene, lane, or keyed record is its https URL on the binding server that hosts it. Bus-internal names (the registry grammar) govern the DDS side and map onto URL paths; they need no registration of their own.
- Identities are DIDs where they cross parties; same-as to a DID is already how the model links them.
- Vocabulary the spec coins (predicates beyond the borrowed sets, basis values, future policy terms) resolves under a namespace the project controls: spatialdds.org/ns/. A name that cannot be dereferenced is a UUID with ceremony; there is no spatialdds: URI scheme and there should never be one.

## 8. Scaling model

Design and practice, kept separate honestly.

The design: the scene is the atom and must fit on one instance; growth is federation by discovery, not a bigger scene. Reads scale like the web scales, by cache: ETag, 304, and CDN-ability are the mechanism, which is why the read contract forbids semantics a cache would break. Writes scale by the one-gateway rule: a lane has one owner, so write throughput is a per-lane ceiling, raised by splitting scenes, not by replicating writers. Heavy payloads ride manifests.

The practice: anecdotal. One $5 instance has sustained the recorded sessions and the public sandbox; nothing has measured claims per second at the validated path, WebSocket fan-out, or journal behavior at gigabyte scale. The characterization batch after 0.8.3 owes exactly those numbers, and this note's write contract gives it the budget lines to measure against.

## 9. Non-goals

- Not the game loop, per section 3.
- Not a safety bus. Records are advisory; an actor's own stack decides.
- Not a command surface for other parties' machines. Command lanes exist for a scene's own instruments; the binding never grows a lane for telling someone else's robot what to do.
- Not a fusion service. The binding carries claims; building a view from them is the consumer's work, or a blessed fuser's, and that argument stays out of the transport.
- Not required. DDS deployments run without any binding; OWM runs on the binding without DDS. The binding is a role either side can take or leave.

## 10. What 1.9 should make normative

The register entry, concretely:

1. The four carrier obligations (section 2) as the definition of any OWM transport, with the web binding and DDS as the two profiled carriers.
2. The write contract (section 5), all six promises, as the normative write surface the current appendix lacks.
3. The scope grammar (section 6) as the credential model, with OAuth client credentials as an informative recommended profile.
4. The canonical URL rule (section 7) as the public naming form.

The rest of this note (tempo boundary, scaling design, non-goals) stays design guidance: binding on this project's implementations, informative in the spec.

Evidence trail: findings 73, 75, 78, 79 (spatialdds-py); the recorded crossing session throughput; the sandbox claims-live operation and its conformance brackets; Appendix N's absence of a write surface, established during the 0.8.2 cycle.
