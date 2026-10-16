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

**Status:** candidate. Raised while adding a service announce to the
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
`spatialdds-web/WEB-BINDING-FINDINGS.md`:

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

**Status:** implemented as an endpoint extension in `spatialdds-web`, labeled
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
* **Per-provider tokens and scopes.** One operator credential publishes
  anything; no notion of a publisher owning its records.
* **Server-set identity fields.** See the design rule above.
* **Upstream WebSocket streaming** for high-rate publishers.

### Evidence

`spatialdds-web`: `spatialdds_web/publish.py`, routes in `gw_http.py`, and five
gates — `golden_publish`, `publish_reject`, `journal_replay`, `publish_live`,
`publish_offswitch`. The last is the one that matters for this entry: with the
surface off, every read is byte-identical to an endpoint built without it.

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
