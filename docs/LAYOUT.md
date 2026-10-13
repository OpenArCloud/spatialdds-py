# Layout

Moved here intact from the README, and brought current with the directories the
storefront batch added.

See also [`WIRE.md`](WIRE.md) for the wire format and the decoding contract,
[`EVIDENCE.md`](EVIDENCE.md) for the gates, and [`RESOLUTION.md`](RESOLUTION.md)
for the `spatialdds://` resolution characterization.

## Layout

```
spatialdds_web/   idlmodel (wire model from embedded IDL), xcdr2 (reader+writer),
                  webjson (canonical JSON), gateway (state, discovery),
                  gw_http (the pinned HTTP and WebSocket surface),
                  publish (the implementation-defined write surface)
webjson/          schemas, serializer tables, golden vectors, MAPPING, PROVENANCE
samples/          two recordings, their inputs, assets, content manifests,
                  and the authored records the publish gates check against
examples/         everything a reader runs: curl, browser console, seven
                  scripts, and the migration pair under examples/migration/
gates/            everything above
tools/            run_gateway.py, author_anchors.py
docs/             WIRE, EVIDENCE, LAYOUT, RESOLUTION, PUBLISH
deploy/           Dockerfile and RUNBOOK for the hosted endpoint, unbuilt
```

`hello_spatial.py` lives in `examples/`, not `samples/`: it is something a
reader runs, not something the gateway serves. `samples/` holds inputs to the
gateway and nothing else.
