# Request and reply over DDS: the convention, not an API

The native layer in 0.7.0 builds no request/reply machinery. That is a ruling
(docs/NATIVE-SDK-CENSUS.md, ruling 4): request/reply is the specification's
open design, and the localization service's successor is already a 1.9
candidate (`directions/spatialdds-1.9-candidates.md`, entry 5). An SDK API now
would freeze a convention in the middle of its redesign. So this page writes
down the convention two codebases already run, so a program can follow it with
the layer's building blocks, and the API waits for 1.9's answer.

The two codebases:

- SpatialDDS-demo's `spatialdds_demo/service_bus.py`, the reference shape;
- openvps's DDS face, `maplocalizer/server/spatialdds/service.py`, which runs
  it against a real localizer.

## Localization (VPS)

The types are `spatial::argeo::VpsRequest` and `VpsResponse`, registered as
`vps_query` and `vps_response` (§3.3.2), on profiles `VPS_REQ` and `VPS_RESP`.
Both are reliable, volatile and KEEP_ALL with no deadline (§3.3.3).

1. **Correlation is the request's `query_id`, mirrored by the response.**
   There is no envelope and no separate correlation id.
2. **Addressing is `service_id`.** An empty `service_id` on a request means
   whoever is listening. A named one may pin a revision as `<id>;v=<revision>`;
   openvps uses the revision for the map a request wants. Routing compares the
   part before `;`. The revision's meaning is the service's business, and a
   wrong revision is an explicit `VPS_FAILED`, not silence.
3. **Both ends check the address.** The responder answers only requests that
   are its own. The client accepts only a reply whose `service_id` is the one
   it asked. Without the second check, a responder that answers everything wins
   on latency alone, with a pose from another map. Measured in the demo on AWS:
   a co-located stand-in beat the real localizer to every request in 0.4 s.
4. **Imagery travels as blobs, by reference.** The request carries `BlobRef`s.
   The bytes go on the blob lane as chunks, each with a CRC32, and the whole
   blob carries its SHA-256 `BlobRef.checksum`. Chunks can arrive before the
   request that names them. A responder parks them, bounded by age and by
   count; openvps uses 20 s and 32 pending. The SDK's `Reassembler` verifies
   both checksums, where openvps verifies only the CRC (filed for openvps).
5. **The client opens its reply reader before it writes the request.** A
   volatile reply written before the reader matched is gone.

**Lane names are open.** The demo and openvps use `spatialdds/vps/query/v1`
and `spatialdds/vps/result/v1`. Those four-segment names appear in the
specification only in an example manifest. §3.3.1 reserves four-segment names
for well-known and profile-defined topics, and §3.3.2's `vps_query` row gives
no template. `spatialdds.dds.registry.topic_name` builds §3.3.1's five-segment
names and will not invent a four-segment one. Whether the example's names are
normative is on the spec queue.

## Coverage queries

The types are `spatial::disco::CoverageQuery` on the well-known
`spatialdds/discovery/query/v1`, and `CoverageResponse` on the query's own
`reply_topic`. The rules are the specification's own (§3.3, normative):

1. The querier creates a reader on `reply_topic` *before* publishing the
   query. The reply writer SHOULD be reliable, volatile and KEEP_ALL.
2. Every response echoes the query's `query_id`. Large result sets page with
   `next_page_token`, and responders SHOULD honour a page token for `ttl_sec`.
3. **Many responders answer one query on one instance.** `CoverageResponse`
   has no `@key`, so under KEEP_LAST(1) the second reply overwrites the first
   before a reader polling at any sane interval takes it, and the querier
   silently sees one service where several answered. The demo's reader
   therefore takes KEEP_ALL. History is a reader-side policy, so this changes
   nothing for anyone else on the topic.

## With the native layer, today

What the layer gives these flows:

- **The well-known query lane.** `p.reader("CoverageQuery")` and
  `p.writer("CoverageQuery")`.
- **Five-segment VPS lanes.** `p.writer("vps_query", stream=…)` and
  `p.reader("vps_response", stream=…)`.
- **The blob halves.** `sd.dds.chunk`, `sd.dds.Reassembler` (which checks
  both the CRC32s and the whole blob's SHA-256) and `sd.dds.blob_ref`, on an
  extension lane the program names, because 1.8 names none.
- **The types and the QoS.** `spatialdds.dds.types` has every type, and
  `spatialdds.dds.qos.reader_qos(profile)` is the §3.3.3 policy set.

What it does not give, deliberately:

- **A reader on an arbitrary topic name.** `CoverageResponse`'s `reply_topic`,
  for example, or the example manifest's four-segment VPS names. The layer
  names topics only from the registry. A program that needs one opens it with
  cyclonedds directly, using the generated type and the layer's QoS, and a
  KEEP_ALL history for a reply lane per rule 3 above.
- **Correlation, addressing and timeouts.** These are the program's, written to
  the rules above, until 1.9 decides the shape.
