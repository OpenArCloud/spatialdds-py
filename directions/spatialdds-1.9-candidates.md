# SpatialDDS 1.9 candidates

1.8 is complete and amendments-only. This is where rows land that are real but
are not 1.8 correctness fixes: additive type changes, new normative text, and
anything whose absence is a fidelity loss rather than a defect.

A row belongs here when it would change the wire or the normative surface. A
row that only needs an implementation, a gate, or an editorial pass belongs on
the working board instead.

---

## 1. Polygon coverage on `CoverageElement`

**Status:** candidate. Raised by the OSCP migration census, where the SSR's
GeoJSON coverage polygon has no lossless target.

**What exists today.** `Announce.coverage` is `sequence<CoverageElement,16>`,
and a `CoverageElement` can express exactly four shapes: a planimetric `bbox`
(`[west, south, east, north]`), a 3D `aabb`, a `circle` (`circle_center[3]` plus
`circle_radius_m`), and `global`. There is no vertex list anywhere in discovery.
The JSON side is identical: §8.1's manifest `coverage` block "MUST follow the
Coverage Model (§3.3.4)" and shares its semantics, so there is no bus-versus-web
asymmetry to exploit. `polygon` appears in `spatial.events` 1.8 and nowhere in
`spatial.disco`.

Evaluation is box-intersection throughout. The informative pseudocode reduces
coverage to `union(bbox, geohash, elements[*].aabb)`, and §3.3.4 already permits
the one curved form to degrade: "for intersects evaluation a circle MAY be
approximated by its bounding box."

**Why it is a gap and not a defect.** The honest-geometry argument that earned
`SpatialZone` its polygon does **not** transfer with the same force. A zone is a
decision surface — inside or outside, consequences follow — so a box drawn
around an L-shaped keep-out zone asserts restricted ground the operator never
restricted. The same reasoning denied a tripwire any area at all. Discovery
coverage is a *search hint*: it answers "might this service have something for
me", and the follow-up query gives the real answer, so an enclosing box
over-reports and costs a wasted request rather than a wrong decision. §3.3.4
says as much in its own vocabulary — "regional hints",
`coverage_source_ids` marking declared coverage as "an approximation of the
union of the sources' coverage", consumers who MAY resolve sources for exact
extents. Coverage is approximate by construction, and nobody acts on it alone.

Two arguments do carry, and neither is about honesty:

- **Fidelity at a migration boundary.** An SSR polygon is data someone authored.
  Mapping it to a bounding box discards it irreversibly, which makes the census
  row lossy by design rather than clean.
- **The 16-element union is a worse approximation than it looks.** A city
  district, a rail corridor or a coastline decomposed by hand into up to sixteen
  boxes is imprecise and awkward to author. The `circle` form exists for exactly
  this reason — the spec preferred letting a producer state the real shape over
  hand-computing a bounding box, "so consumers can recover the exact footprint."
  That is the closest precedent, and it is a fidelity-and-convenience argument.

**Smallest change, characterized.** Mirror the zone addition, whose machinery is
already proven and whose `Vec2` primitive already exists:

- `boolean has_polygon` plus `sequence<Vec2, N>` appended at the end of
  `CoverageElement`, per APPENDABLE. `N` well under the zone's 256 — discovery
  pages are deliberately small, and a 16-element coverage sequence multiplies it.
- The zone's three rules transfer verbatim: the polygon takes precedence over
  bounds for containment; consumers that understand polygons MUST prefer them;
  and `bbox`/`aabb` MUST still carry the polygon's axis-aligned bounding box, so
  a bounds-only consumer keeps working and keeps **over**-reporting rather than
  under-reporting. Implicit closure, final vertex not repeated.
- The `intersects` predicate gains a polygon case where it currently only unions
  boxes, and the §3.3.4 pseudocode is updated with it.

**Not drafted.** No IDL or spec text written; this is the characterization only.

**Affects:** `idl/v1.9/discovery.idl`, §3.3.4, the §8.1 coverage block by
reference, and the conformance suite's coverage checks. Closes the
`geometry (GeoJSON polygon)` row in `oscp-to-spatialdds-mapping.md`, which is
GAP (fidelity) until this lands and HOME after.

---

## 2. `manifest_uri` on `GeoAnchor`

**Status: LANDED in the 1.8 revision 2026-10-15** (SpatialDDS-spec CHANGELOG,
"Version 1.8 Revision 2026-10-15: GeoAnchor content reference"). `GeoAnchor`
gained an optional `SpatialUri manifest_uri`, APPENDABLE and wire-compatible;
Appendix C carries the binding semantics and §8.2.1 the manifest field. Kept here
as the record of the candidate; the revision supersedes it. Entry does not vanish.

**Status (as raised):** candidate. Raised by the anchor-bearing sample directive, where
"at least two anchors carry content refs" turned out to be inexpressible.

**What exists today.** No anchor type in 1.8 can reference content.

```
GeoAnchor        8 members; link-capable members: NONE
AnchorEntry      7 members; link-capable members: NONE
ContentAnnounce  16 members; link-capable members: SpatialUri manifest_uri
```

`GeoAnchor` is `anchor_id`, `map_id`, `frame_ref`, `geopose`, `method`,
`confidence`, `checksum`, `cov`. No refs, no assets, no `manifest_uri`, and no
`MetaKV` or `attributes` bag — so there is not even an escape hatch to abuse.
`AnchorEntry` and `AnchorSet` are the same. `ContentAnnounce` carries
`manifest_uri` but has no anchor reference, so the association cannot be made
from the other end either.

The shape exists one layer up. An **anchor manifest** (§8.2.1) can carry
assets, because §8.1's envelope `assets[]` is open to every `rtype` and
`AssetRef` has exactly the `uri` / `media_type` / `hash` required. What is
missing is the step from a served `GeoAnchor` to that manifest.

**Smallest change.** `SpatialUri manifest_uri` appended to `GeoAnchor`,
APPENDABLE, mirroring `ContentAnnounce.manifest_uri` exactly: same type, same
"MUST be a `spatialdds://` URI" rule, same resolution path through §7.5 to a
§8.2.1 anchor manifest whose envelope carries the assets. One member, no new
vocabulary, no new resolution machinery.

**Why `manifest_uri` and not `content_refs`.** The provisional owm `Entity` has
`sequence<string, 8> content_refs`, so the pattern exists in the tree and is the
obvious alternative. It is the wrong one here: it duplicates the manifest
layer's job and bypasses §7.5. Keeping §7.5 as the one resolution path is worth
more than saving a hop.

**Two inventions declined while building the sample**, recorded because the
reasoning is the useful part:

- *A `content_refs` field on `GeoAnchor`.* A spec change dressed as a sample.
- *Constructing the manifest URI by convention.* The grammar is
  `spatialdds://authority/zone/type/id`, and a client that fetched
  `/{domain}/frames/geo_anchor/v1/{anchor_id}` could assemble authority and zone
  from the endpoint and path. Nothing in §7.5 says it may, and every other type
  needing a manifest carries the URI explicitly. **An undocumented construction
  rule is a dialect**, and a dialect is what a normative binding exists to
  prevent.

**Consequence for the conformance suite.** §8.2's scenario reads "GET anchors
near a geohash → fetch **an anchor's asset by reference** → resolve on device."
That check is written against a link 1.8 does not have, so no recording can
un-skip it. Per the sample directive's own clause, that is a finding about the
scenario definition rather than something to make pass by adjusting the
recording. §8.2 stays skipped with this entry as its stated reason, and the
scenario is **not** reworded to something passable — it waits for the field it
was written for.

**The structural parallel, and a pattern worth hunting.** This is the second
instance of the same shape. owm finding 1: an area entity "describes its
footprint with `events::SpatialZone`" and `Entity` has no field that can carry
*which* `SpatialZone`. Here: the suite and the directive both say an anchor's
asset is fetched by reference, and `GeoAnchor` has no field that can carry
*which* manifest. In both cases **the specification points at a type and
provides no field to name the instance.** Two instances make it worth a
systematic pass in the 1.9 design: wherever prose says "described by" or
"referenced by", check that a field exists to carry the reference.

**Not drafted.** No IDL or spec text written.

**Affects:** `idl/v1.9/core.idl`, §8.2.1 by reference, the conformance suite's
§8.2 scenario, and the `fountain-anchors` sample, whose content entries are
positioned by their own pose and coverage until this lands.

---

## 3. `durability` on `TopicMeta`

**Status: LANDED in the 1.8 revision 2026-10-15** (SpatialDDS-spec CHANGELOG,
"Version 1.8 Revision 2026-10-15", second item). `TopicMeta` gained an optional
`string durability` carrying the DDS kind, APPENDABLE and wire-compatible, so a
bridged gateway can populate the N.7-required manifest durability from the bus
type (closes finding 17). Kept here as the record; the revision supersedes it.
The revision window is now closed; entry 1 and everything after is 1.9.

**Status (as raised):** candidate. Raised while adding a service announce to the
fountain-anchors recording. **The bridged deployment shape's first concrete
blocker, found before anyone built one.**

**What exists today.** Appendix N.7 makes `durability` REQUIRED on a manifest
topic entry, carrying `"VOLATILE"` or `"TRANSIENT_LOCAL"` — the DDS kinds
verbatim, which is how flag 6 was resolved. A gateway fronting a bus builds
that entry from `Announce.topics`, which is `TopicMeta`:

```
TopicMeta: name, type, version, qos_profile, target_rate_hz, max_chunk_bytes
```

No durability member. And `qos_profile` cannot stand in for it: it is a
free-form profile *name* in §3.3.2 (`VIDEO_LIVE`, `POSE_RT`), and carrying
durability there is precisely the overload flag 6 removed from the manifest a
few days ago. So a bridged gateway has nowhere to read durability from and
cannot populate a REQUIRED field without inventing one.

**Why it has not bitten yet.** The standalone gateway in this repository
derives its topic list from the resources it actually serves and knows each
one's durability from its own table, so it never consults `TopicMeta`. That is
an artefact of the standalone shape. A bridged gateway, whose whole job is to
re-publish what an `Announce` told it, hits this on its first manifest.

**Smallest change.** `string durability` appended to `TopicMeta`, APPENDABLE,
taking the same two DDS kinds as N.7, so the bus type can say what the manifest
must. One member, no new vocabulary, and it mirrors the N.7 resolution exactly
rather than inventing a parallel one.

**Why this one is worth noticing as a process result.** Certification needs the
same conformance suite green against a bridged endpoint as well as a web-native
one, and the bridged half has been standing on the board as known-uncovered
since step 6. This is that uncovered half stating its first requirement in
advance, from a sample that was built for an entirely different purpose. That is
the cheapest possible moment to learn it: before a bridged gateway exists, while
the fix is still one appended member rather than a deployed interface.

**Not drafted.** No IDL or spec text written.

**Affects:** `idl/v1.9/discovery.idl`, Appendix N.7 by reference, and any future
bridged gateway. Unblocks nothing in this repository, which is the point.

---

## Also dispatched, and not candidates

Recorded here so the 1.9 pass does not rediscover them as features. Both are
errata to published text, tracked in
`spatialdds-py/WEB-BINDING-FINDINGS.md`:

- **N.3 states no encoding rule for an instance key** (finding 13). The
  specification's own `GeoAnchor.anchor_id` example contains the path
  separator, and unencoded it is unroutable. One sentence.
- **The well-known discovery topics have no N.3 path** (finding 16).
  `spatialdds/discovery/announce/v1` has four segments where N.3's mapping
  assumes §3.3.1's five, so `Announce` and `Depart` on their well-known topics
  cannot be addressed as web resources. The fountain sample escapes it by
  scoping its announce to the domain; a bridged gateway reading the well-known
  topic cannot. One sentence, most simply mapping a short topic into the
  well-known namespace.

And two that are the suite's own, not the specification's: the pair of
conformance checks that fail an endpoint for being homogeneous (finding 14),
whose verdict should be UNEXERCISED, and §8.2's skip reason predating the
finding that explains it (finding 15).

## 4. A web-binding write path

**Status:** implemented as an endpoint extension in `spatialdds-py`, labeled
implementation-defined everywhere it appears, and offered here as evidence
rather than as settled text. 1.8 specifies no publish verb and forbids none, so
nothing about this belongs in published text by erratum: an erratum fixes a
contradiction and this is a feature.

### The design rule that outlives the batch

**Identity fields come from the credential, never from the request body** —
provider, tenant, and server-set timestamps. The OSCP census's platform-set SCR
fields are the precedent: a registry that lets a publisher name itself has no
way to tell a publisher's claim from a fact. The first implementation does
**not** honour this: `provider_id` and `org` are read from the body and the
server sets no field. Recorded as a gap, not as a feature.

### Verbs, normative-shaped

A binding MAY offer a write surface. Where it does:

* `PUT` on the instance resource defined in N.3, body in N.1 canonical JSON,
  keyed by the record's own `@key` member under the N.3 percent-encoding rule.
  `201` on create, `200` on replace. The response body MUST be the served
  canonical form and MUST carry the instance's `ETag`.
* `DELETE` on the same resource disposes the instance: it leaves the latched
  set and the dispose streams per N.5. `204`.
* `If-Match` MUST be honoured when present; a mismatch is `412`. `*` matches
  any existing instance.
* Every rejection MUST be a problem document per N.8: `400` for a body that is
  not a valid record of that type, `401` without a credential, `403` for a
  record carrying URIs outside the endpoint's authority, `405` for a topic the
  endpoint does not accept writes on, `412` for a failed precondition.
* Write routes MUST be operator-scoped per N.4, hence `no-store`.
* A published instance MUST be indistinguishable from a replayed one to a
  read-only client.

**Open question, deliberately not settled by the implementation:** an absent
`If-Match` is an unconditional replace. That is what the first implementation
does. Whether a registry write should require a precondition — whether blind
overwrite should be opt-in rather than default — is a question for 1.9, and the
implementation's choice is not an argument for it.

### Resolution of published content

A published content manifest MUST be resolvable through §7.5.2 the moment it
lands, with no second mechanism: the implementation keys manifests by the full
URI each declares as its `id` and publishes into the same store the resolver
reads, and the N.4 `ttl_sec` precedence applies to a published manifest exactly
as to any other.

### Persistence

A write surface SHOULD journal accepted writes as a typed recording carrying
its own IDL, so that a backup is readable by any SpatialDDS tool. The
implementation does, and a gateway given only a journal learns its types from
it.

**Gap this exposed: 1.8 has no withdrawal record addressable as a web
resource.** `Depart` is `{service_id, stamp}` and covers services only; nothing
expresses a content withdrawal; and `Depart`'s well-known topic has four
segments, so it cannot be addressed under N.3's five-segment grammar at all
(see web-binding finding 16). A journal therefore cannot record a dispose as a
typed message. The implementation uses a labeled MCAP metadata record and says
so. **A 1.9 candidate in its own right:** either a withdrawal record that
covers content, or an N.3 rule for addressing the well-known discovery topics,
or both.

### Deferred, recorded as gaps rather than silently absent

* **Leases and expiry.** A registry must shed dead publishers; SSD parity
  requires it. Both records carry `ttl_sec` and nothing enforces it.
* **Per-provider tokens and scopes.** Built 2026-10-24 as *lanes*: tokens
  scoped to scenes, measured below under "Lanes". Per-record ownership inside
  a scene remains deferred.
* **Server-set identity fields.** See the design rule above.
* **Upstream WebSocket streaming** for high-rate publishers.
* **Anchor provenance.** `geo_anchor` joined the publishable kinds after the
  1.8 revision, under this same deferral rather than ahead of it: nothing
  records who published an anchor, or whether it refines an earlier one. A
  self-describing anchor published to a web registry, able to bind content at
  its own stated precision, is the open-anchors direction taking its first
  real step — which is what stops the two identity questions above from being
  theoretical. The honesty line the implementation carries instead is that
  **method is the claim level**: a published anchor asserts a pose at the
  confidence its own `method` and `cov` declare, and publishing does not
  upgrade that claim.

### Lanes, measured (2026-10-24)

What the multi-tenancy batch found that normative text would need to say, each
with where it was measured:

* **A URI's scene segment and a resource path's first segment are one
  namespace, and 1.8 does not say so.** The seed recording named its manifests
  `…/south-mall/…` while its topics used `ut-austin-south-mall`, and nothing
  compared them (spatialdds-py finding 48). A write surface that scopes
  anything by scene needs §7.5's URI scene segment to *be* §3.3.1's domain
  segment, stated, or the resolve surface and the collections are two
  namespaces with one name. Measured: `gates/tenancy.py`, the manifest
  cross-lane refusal.
* **A withdrawal has two halves.** Disposing an announce leaves its manifest
  resolvable (finding 44, observed live on demo.spatialdds.org 2026-10-24); the
  binding names no verb for the manifest half. This endpoint uses
  `DELETE /spatialdds/resolve?uri=…`. If 1.9 specifies a write path, it
  specifies both halves or neither.
* **`403` versus `401` is a lane fact.** Unknown or absent credential: `401`
  with `WWW-Authenticate`. Known credential, wrong scene: `403` problem
  document naming the scene. A scene that exists nowhere: `403`, distinct
  type, so a typo is a refusal and not a new scene (finding 46).
* **Existence is a grant, not a write.** An empty lane exists because a grant
  names it; minting a scene is a separate permission. Without that split, an
  operator could not give a party an empty lane without also giving every
  wildcard holder the power to create scenes by typo.

### Evidence

`spatialdds-py`: `spatialdds/server/publish.py`, routes in `gw_http.py`, and
five gates — `golden_publish`, `publish_reject`, `journal_replay`,
`publish_live`, `publish_offswitch`. The last is the one that matters for this
entry: with the surface off, every read is byte-identical to an endpoint built
without it.

**Updated 2026-10-16, package 0.2.0.** The write surface is now the headline
rather than an extension: a registry starts empty, takes its types from the
specification pin, and is published into in one call per record kind
(`gates/fresh_start.py`, `gates/builders.py`, `docs/MYTOWN.md`). Two things
that entry 4 deferred moved from deferred-and-untested to
deferred-and-exercised-around:

* **Journalled manifests.** A published content manifest is now a journalled
  record, because an empty registry has no `--manifests` directory to survive
  a restart by (finding 34). The journal remains a plain recording.
* **Identity, still untouched.** The builders fill no `provider_id` and no
  `org`, by design, and say so in their own documentation. A registry that
  vouched for either would be making a claim nobody authorised.

**All four deferrals stand, and are stated where a reader meets them** --
leases, per-provider scopes, server-set identity, upstream subscriptions are
listed in `docs/MYTOWN.md`'s closing section and in the package's README, so
nobody has to read this file to learn what a registry does not do.

**Updated 2026-10-29, package 0.8.0: world lanes.** The write surface now
takes the world model's `entity` lane and a deployment's own keyed types
(`extend()`-registered, `serve --extension`). Normative-shaped, what it adds to
the verbs above:

* A type with a basis and a lifecycle (`basis`, `state`, `state_reason`,
  `stamp`) is withdrawn by `DELETE` as a writer withdraws it on the bus: a
  terminal sample, RETIRED with the reason, then the dispose. A record type
  disposes alone, as before.
* Freshness for such types is entry 12; the asserter of a claim arriving over
  HTTP is entry 13.
* A gateway joined to a DDS domain writes what its world lanes accept there,
  from its own participant, so bus readers see ordinary samples. Measured: a
  DDS reader on another participant received each HTTP write as a sample whose
  N.1 projection equals what the gateway serves (`gates/entity_publishing.py`).

Two of the deferrals above move: leases now exist for world lanes (entry 12;
records' `ttl_sec` is still unenforced), and one identity field now comes from
the credential, a claim's asserter (entry 13; `provider_id` and `org` still
come from the body). Gates: 49 of 49 with `entity_publishing`; the
conformance suite against `serve --demo`, 15 checks: 14 passed, 0 failed, 1
skipped (§8.2, unexercised: the demo endpoint is not an authority), the same
as 0.7.4.

## 5. The GeoPose Protocol successor, as a service profile

**Status:** scoped, not drafted. A prerequisite census comes first, recorded
below. This entry also sets the pattern for every later service profile, which
is part of what it is for.

**The shape of the answer, decided:** the OSCP GeoPose Protocol's successor is
a **localization service profile over the web binding, not a mechanism in it.**
A separate profile document that depends normatively on SpatialDDS 1.8 and
Appendix N and **adds nothing to either**. If the profile needs a new verb, a
new well-known path, or a new envelope field, that is a finding against the
binding to be filed and resolved there — not something the profile may define
for itself. A profile that extends its own substrate is a fork.

### Two invocation modes

**Single-shot localize.** A `POST` to an endpoint the service manifest
advertises, answering with a typed `GeoPose` carrying covariance, in N.1
canonical JSON. Advertised, not constructed: the same hypermedia rule the
latched resources follow, where a client reads a URL out of a manifest rather
than assembling one.

**Continuous session.** One WebSocket: frames up, `geo.fix` down. This is the
**first purposeful use of the upstream lane** that entry 4 deferred — until now
the WebSocket has been read-only in this binding, and a publisher streaming
upward had no stated semantics.

The session's semantics are **ephemeral, and deliberately distinct from the
registry write surface**: nothing is declared, nothing is journaled, there is
no lease. A localization session leaves no trace in the registry and needs
none, which is the cleanest available answer to entry 4's deferred leases
question for this case — it does not need one. Writing that distinction down is
half the value of the entry, because the two lanes will otherwise be conflated
by anyone who sees "WebSocket" and "upstream" in the same sentence.

### Conformance

Claimed by a **versioned token in the manifest's profiles list**, and checked
**only against claimants**. An endpoint that does not claim the profile reports
**UNEXERCISED** rather than passing or failing — the vocabulary already adopted
for exactly this, and the reason the conformance suite can grow profile checks
without failing every endpoint that is not a localizer.

**"Could not localize" is a result shape, not an HTTP fault.** A service that
answers honestly that it does not recognise the place has succeeded at its job.
Appendix L already treats refusal as conformance; this carries the same rule
into the response body rather than the status line.

### Prerequisite, before any design draft

**A field-level census of the OSCP GeoPose Protocol's request and response
against 1.8 types**, by the same method and in the same table shape as the
SSR/SCR census in `directions/oscp-to-spatialdds-mapping.md`.

(That census is local planning material and is not published: `directions/` is
ignored except for this file. A reader of this repository cannot follow the
reference, which is worth knowing before treating it as a citation. If the
census is what this entry depends on, publishing it is the cheaper fix than
summarising it here.)

That file already has a *GeoPose Protocol* section, and it is four rows at the
level of "Request / Response / Priors / Quality claim". That is the right
summary and it is not a census: it does not say which 1.8 member carries each
GeoPose Protocol field, which fields have no home, and which 1.8 members a
localization request would leave empty. The SSD and SCD censuses were done at
that depth and both produced findings; this one has not been, and a design
draft written before it would be guessing.

One reconciliation the census owes: the existing mapping row says the request
goes "per the keyed request pattern over N.6", while this entry says "a `POST`
to an endpoint the service manifest advertises". Those may be the same thing —
an N.6 command endpoint named in a manifest — or they may not, and the
difference decides whether the profile adds anything to Appendix N. The census
is where that gets settled, not the design draft.

### Evidence pointers

* The **VPS manifest's `request` and `response` blocks**, derived from
  `manifests/v1.8/vps_manifest.json` into
  `samples/published-records.input.json`. They already name topics and roles
  for a localization exchange — `features_topic`, `image_blob_role`,
  `prior_topic`, and `rich`/`minimal` responses — which is the closest thing
  the specification has to this profile's wire shape today.
* The **`austin-vps` published record**, live over the write surface and shown
  in full in `docs/WALKTHROUGH.md` step 8. A claimant's manifest is what the
  conformance check would read, and that is what one looks like.

### The terminology hazard, to settle at design time

"A versioned token in the manifest's profiles list" is ambiguous today, because
**four** different fields already hold a profile-ish string, and they do not
agree on form:

| where | form | example, from the live records |
|---|---|---|
| `Announce.caps.features` | free-form string | `web-binding/json` |
| `Announce.caps.supported_profiles[]` | structured `{name, major, min_minor, max_minor}` | `spatial.core` 1.8–1.8 |
| `Announce.caps.preferred_profiles[]` | same structure, **empty in every record here** | — |
| the service manifest's top-level `profiles[]` | free-form label | `Core`, `SLAM Frontend`, `AR+Geo` |

And a fifth string, the manifest envelope's `profile`
(`spatial.manifest/1.8`), means something else again: the schema of the
document rather than a capability of the service.

So the profile's claim token must say which of these it is, and the honest
candidate is `caps.supported_profiles` — it is the only one already carrying a
version, which is what "versioned token" requires, and the free-form lists
cannot express a minor range. That choice needs ratifying rather than
assuming, and whichever is chosen, the unused `preferred_profiles` should be
given a stated meaning or an explicit "not used by this binding", because a
field that exists and is always empty is a field the next implementer will fill
with something.

## 7. A manifest member that says a lane accepts commands

**Status:** candidate. (Numbered 7: entry 6 is reserved for the relationship type drafted in
`SpatialDDS-demo/docs/relationship-candidate-1.9.md`.) Raised by implementing N.6 in the reference gateway,
2026-10-17, which is the first implementation to need it.

**What exists today.** N.6 says a command is a POST to the resource it acts
on, and scopes itself to "where an endpoint exposes a command surface". N.7
says what each `service.topics` entry MUST carry: `name`, `type`, `version`,
`durability`, `url`, `access`, plus optional `qos_profile` and `liveliness`.
None of those can say which lane, if any, accepts a verb.

**So a client cannot discover a command surface; it can only probe for one.**
POST and read the refusal. That is the opposite of what N.7's `url` member was
added for — the manifest exists so a client follows links instead of
reconstructing the binding — and it is worse than a missing field, because the
probe is a write-shaped request against an endpoint that may be
operator-scoped. A client without a credential cannot even run the probe.

**What the reference implementation did.** Added `accepts_commands: true` to
the topic entry of any lane with a registered handler, omitted entirely
otherwise, so a manifest from an endpoint with no verbs is byte-identical to
one from before the surface existed. Documented in `docs/WIRE.md` as
implementation-defined and gated in `gates/commands_n6.py` to appear on
exactly the handled lanes. **It is not proposed as the name** — a single
implementation should not mint manifest vocabulary — only as evidence that
something is needed and as one shape that works.

**The design question worth settling before a field list.** Whether command
acceptance is a property of a *topic* (this member) or of the *service* (a
`service.commands` block listing accepted types). The topic-entry version
suits a client that already holds the entry it wants to POST to; the
service-level version suits a client deciding whether this endpoint is worth
talking to at all. The reference implementation chose per-topic because N.6
locates the command at a resource, but the service-level shape composes better
with §8.2.3's existing blocks.

**Evidence.** `gates/commands_n6.py` passes ten checks over the surface,
including that the advertisement and the behaviour agree. The demo repo's web
bridge is the first external consumer and is blocked on this surface landing;
its census records the whole measurement
(`SpatialDDS-demo/docs/bridge-1.8-census.md`, finding 4).

## 8. A well-known path for the REST surface's OpenAPI document

**The question for 1.9:** whether Appendix N.2 blesses
`/.well-known/spatialdds/openapi` (OpenAPI 3.1, YAML) beside `schemas`, with
spatialdds-py as the reference implementation.

**What exists (2026-10-24).** `tools/gen_openapi.py` generates the document
from the gateway's own type tables, the published JSON Schemas and the write
rule; `gates/openapi.py` fails on drift and checks every registered route is
documented and every documented operation is a route. The gateway serves it
with its own base as the server, plus a script-free reference page at
`openapi.html`. The WebSocket streams (N.5) are described in prose on each
collection path, not modelled; AsyncAPI would be the formal home.

**Measured, for whoever writes the text.**

* **The published schemas are one model.** Over the 112 files, every `$defs`
  copy of a type equals the type's own file except for `title`. A generator can
  hoist them into one `components.schemas` with no choice to make; normative
  text could say so, and a schema generator could be held to it.
* **`const` alone is not enough for today's tooling.** The schemas tag union
  branches with `{"const": "COV_POS3"}`, which is correct 2020-12 and correct
  OpenAPI 3.1. openapi-generator 7.10.0's Python client ignores `const`, so a
  GeoAnchor's `cov` matched all five `CovMatrix` branches and the read flow
  failed. A one-value `enum` beside each `const` fixed it. Not a spec defect;
  worth one line wherever the binding talks about generated clients, and an
  argument for the schema generator emitting the `enum` twin itself.
* **The scene rule and the write refusals are documentable from one table**
  (`tenancy.WRITE_REFUSALS`, `limits.REFUSALS`), which is what keeps a
  generated document honest about writes.

## 9. Native participants: what the SDK census found the spec does not say

**Origin.** These come from the native-SDK census of four codebases
(spatialdds-py `docs/NATIVE-SDK-CENSUS.md`, 2026-10-24) and from building the
SDK at the `v1.8` tag. Each sub-entry is a question for 1.9, with the evidence
and what the SDK does meanwhile.

### 9.1 A standard reason for a removal, on types that have no member for one

§2.14 asks every RELIABLE + TRANSIENT_LOCAL keyed instance's removal to be "a
final sample carrying the instance's terminal state and a human-readable
reason", then a dispose. N.5.2 asks the same over the web. But these carry
no member for a reason:

- `Announce`
- `ContentAnnounce`
- `GeoAnchor`
- `SpatialZone`
- `CrossingLine`

Only `owm::Entity` has `state` and `state_reason`. A participant can always
send the terminal *state*, by rewriting the instance's last sample before the
dispose. It cannot put the *reason* on the raw DDS wire.

**Ruling recorded (James, 2026-10-24).** Never refuse the removal over a
missing nicety. Send the terminal sample and dispose for every keyed type, and
carry the reason wherever a carrier exists:

- the type's own member when it has one;
- the recording's metadata, always;
- the log, always.

**The 1.9 question:** a standard optional reason member on the removable
latched types, or a tombstone-reason convention (for instance, a well-known
`Removal` record keyed on the removed instance), so the reason crosses DDS too.
This joins the typed-removal-record direction already noted for 1.9. It is the
same gap, seen from a native writer rather than from the three web removal
conventions.

**The precedence part, which is new.** §3.3's departure rule (dispose
`Announce`, then publish `Depart`, with no terminal sample) is a second removal
rule for the type §2.14 also covers. The SDK does both, terminal sample then
dispose then `Depart`. The spec should say which governs.

### 9.2 Writers of latched keyed types should not autodispose

§3.3.3 fixes four policies and says "other DDS policies MAY be tuned".
§2.14 says loss of liveliness is not removal. DDS's default
(`autodispose_unregistered_instances = TRUE`) turns liveliness loss *into* a
dispose on the wire, before any consumer can obey §2.14. Measured: a
SpatialDDS-demo writer killed with `kill -9` reached its reader as
`NOT_ALIVE_DISPOSED` ten seconds later, at lease expiry. With the policy off,
the same kill is `NOT_ALIVE_NO_WRITERS` (`gates/native_lifecycle.py` holds
both arms). Three of the four codebases shipped the default.

**Candidate:** writers of RELIABLE + TRANSIENT_LOCAL keyed types SHALL set
`autodispose_unregistered_instances = FALSE`. It is writer-local, so no
reader's matching changes.

### 9.3 Registry completeness

The §3.3.2 completeness rule makes a topic-bearing struct without a row a spec
defect. Measured at `v1.8`:

- **Published as topics with no row:**
  - `GeoAnchor` (the web binding's `frames/geo_anchor/v1`);
  - `FrameTransform` (`frames/frame_transform/v1`, in the demo and
    spatialdds_ros);
  - `BlobChunk`, whose lane has no topic, row or profile anywhere (9.6).

  The well-known discovery types have their own table.
- **Rows that name no profile:**
  - `navsat_status` and `entity_binding` spell no QoS at all;
  - `model_entity` spells RELIABLE + TRANSIENT_LOCAL, KEEP_LAST(1), which four
    §3.3.3 profiles carry identically (GEOM_TILE, MAP_META, ZONE_META,
    SENSOR_META);
  - `model_pose` and `model_command` spell durability and history but no
    reliability.

  The SDK refuses all five without an explicit `qos_profile`, and lists the
  profiles that fit. SpatialDDS-demo's `owm-1.8-spec-queue.md` already carries
  the `MODEL_LATCHED` naming half.

### 9.4 What "caught up" means for a native reader

N.5 gives the web binding an explicit `end_of_replay`. DDS states nothing
equivalent: `wait_for_historical_data` covers the writers matched at that
moment, and discovery is asynchronous. The SDK's `EndOfReplay` means
"complete for the writers matched when it fired", after letting the matched
count settle. A latched writer discovered later is just more samples. Should
the spec define the native boundary, so a bridge from DDS to N.5 has one to
translate?

### 9.5 The VPS lanes' names are only in an example

`spatialdds/vps/query/v1` and `spatialdds/vps/result/v1` appear only in an
example manifest. They are four-segment names, which §3.3.1 reserves for
well-known and profile-defined topics, and `vps_query` / `vps_response` have
no template. The demo and openvps both use them. Are they normative, and if so
where? Request/reply is otherwise documented as a convention
(`docs/REQUEST-REPLY.md`), pending entry 5.

### 9.6 The blob lane is unnamed

`BlobChunk` and its partial-delivery rule are normative, but no topic, registry
row or QoS profile carries chunks. The demo chose `spatialdds/blob/chunk/v1` on
GEOM_TILE, and openvps's chunk reader requests TRANSIENT_LOCAL, so a volatile
client writer silently never matches. The SDK names no lane, and a program
uses an extension lane of its own.

### 9.7 A non-finite double has a DDS form and no N.1 form

*Origin: James's ruling on spatialdds-py finding 64, 2026-10-24: "refuse
loudly, never tolerate, never die unnamed."*

N.1's canonical JSON is silent on IEEE specials. Its mapping row says "finite
only; NaN/Inf rejected", which tells a web publisher what not to send. DDS
carries NaN and both infinities, and the spec says nothing about what a
gateway serving a recording, or a bridge from DDS, does with a sample that
carries one. Every implementation of the binding will meet that question, and
the answers available are not equivalent:

- **Refuse the source, naming the offender.** This is the reference
  gateway's ruling at 0.7.0. It refuses the recording with the topic, key and
  member (`RecordingRefused`).
- **Drop the sample and serve the rest.** This serves a world the source does
  not describe, and the ruling rejected it.
- **Emit the token anyway.** The reference gateway did this, unnoticed, for
  streamed samples before the ruling. It sent `Infinity` on the WebSocket,
  which is not JSON.
- **Substitute** `null` or a sentinel. That gives a value with no basis.

Proposed: N.1 states that a non-finite double has no JSON form, that a binding
MUST NOT emit one in any form, and that a binding meeting one in a DDS-side
source refuses that source with a diagnostic naming the topic, key and member
rather than serving it. A native writer refuses at write time, which the SDK
already does.

## 10. An announce has no member that says who relayed it

*Origin: the ruling on spatialdds-py finding 67 (S3), 2026-10-24: relaying
someone else's announce "must be explicit re-publication under the relay's
own writer identity, never impersonation of the original asserter".*

Bridges carry announces between transports: MQTT, recordings, another bus.
`Announce` is keyed on the asserter's `service_id`, and nothing in it says
the copy a reader holds came through someone else. The DDS writer that sent
it is different, but that identity is invisible to a JSON consumer, to a
recording, and to anything downstream of a second hop. So a relayed claim
and a first-hand one read the same.

The reference SDK marks a relayed announce with a `relayed_by` hint naming
the relay's `service_id` (`AnnounceRelay`, 0.7.1). It refuses to relay an
announce that already carries one, so relays do not chain, and it never
disposes what it carried on its own exit. A hint is a convention, though,
not a member: nothing obliges another implementation to set it, or a
consumer to read it.

Proposed: an appended `relayed_by` member on `Announce` (empty for a
first-hand announce), with §2.14's removal rule extended. A relay removes
only its relayed copy, and a consumer weighs a relayed announce as a claim
the asserter made, not one the relay made.

## 11. A templated lane has no web form

*Two origins, 2026-10-25, both in spatialdds-py finding 70:*
- *the reference SDK refused the lane (S15, fixed in 0.7.3);*
- *the reference gateway cannot serve it (not fixed, because it is this
  decision).*

§2 recommends a lane the web binding cannot carry. A PlannedTrajectory
"SHOULD be advertised on the topic
`spatialdds/<scene>/plan/<agent_id>/trajectory/v1`". §3.3.1 makes profile
templates normative where they are defined and lets them "use additional
path segments". But N.3 maps exactly §3.3.1's five segments, or four, to a
resource path, and gives a longer name no path at all. So the plan
trajectory is bus-only by an accident of path grammar, not by anyone's
choice. A recording that carries it serves no resource for it.

Silence is the problem: every gateway will improvise, and two improvisations
will disagree on the URL of the same lane. 1.9 should do one of these:
- define the path mapping for templated topics (for instance, the extra
  segments joined into the stream segment, or appended before the version);
- say explicitly that templated lanes are bus-only and that a gateway omits
  them from its manifest.

## 12. Freshness for an observed claim with no writer liveliness

*Measured origin: spatialdds-py 0.8.0 (2026-10-29), `gates/entity_publishing.py`.*

§2.14 lets a reader tell a stale claim from a withdrawn one by the writer's
liveliness. A claim arriving over HTTP has no writer liveliness: a request
ends and says nothing about the next one. 1.8 is silent on what a web write
of an observed thing promises, so every gateway will either fake liveliness
or let an observation stand forever. The contract implemented, offered as
text:

* **Basis decides.** A DECLARED or AUTHORED write is a declaration and holds
  until withdrawn. An OBSERVED or DERIVED write declares a refresh interval
  (`?refresh=<seconds>`), and is written again within it.
* **The gateway withdraws what lapses, as the writer.** It owns the DDS
  writer, so a lapse is a deliberate removal (terminal sample, then dispose,
  reason `refresh lapsed`), never a disposal synthesized on another
  participant's behalf.
* **No interval on an observed write is refused**, with a problem document
  naming the rule; an interval on a declaration likewise.
* **A restart re-arms the interval** from the restart: the journal records
  each observed write's interval.

Measured: a 2 s interval unrefreshed was withdrawn 2.05, 2.35 and 2.05 s
after its PUT in three runs, terminal sample then dispose on a WebSocket
subscriber and on a DDS reader alike; a declared entity under the same
silence stayed; re-PUTs every 0.5 s kept a 1 s interval alive for 5 s; a
curl session against `serve` saw a 5 s interval withdrawn about 4.9 s after
its last PUT. Failable: with the gateway's reaper stopped the lapse check
fails, and with the basis rule removed the basis, lapse and declared checks
fail.

Open for 1.9: whether the interval belongs in the request (as here), in the
type (a member), or in the lane's QoS, which DDS would spell as a lifespan.

## 13. An HTTP claim's asserter is its credential's principal

*Measured origin: spatialdds-py 0.8.0 (2026-10-29), `gates/entity_publishing.py`.
Extends entry 4's design rule ("identity fields come from the credential").*

A bus claim's asserter is grounded in the participant that wrote it. Over
HTTP the only thing a gateway knows about who is speaking is the bearer
token, so the 0.2 design's `asserter`, and any member that names who makes a
claim, is grounded there:

* an empty asserter is stamped with the token's principal;
* any other value must equal it, or the write is refused (`403`, naming both);
* an endpoint with no credentials has nothing to ground a claim in and refuses
  claims rather than accept an asserter nobody vouched for.

Measured: principal bob asserting as alice refused `403
asserter-not-principal`; alice's empty asserter stored as `alice`; a single
`--token` endpoint stamps `operator`. Failable: with grounding that verifies
nothing, both checks fail.

Open for 1.9: whether the principal's name is the asserter, or a URI the
credential maps to (provider plus name), so that asserters from two
gateways cannot collide.
