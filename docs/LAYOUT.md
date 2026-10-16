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
examples/         everything a reader runs: curl, browser console, eight
                  scripts, and the migration pair under examples/migration/
gates/            everything above
tools/            run_gateway.py, author_anchors.py
docs/             WIRE, EVIDENCE, LAYOUT, RESOLUTION, PUBLISH,
                  WALKTHROUGH
deploy/           Dockerfile and RUNBOOK for the hosted endpoint, unbuilt
lib/spatialdds/   the `spatialdds` client package: its own pyproject, its own
                  README, and a vendored copy of the serializer tables
.github/          one workflow, inert: trusted publishing for that package
```

`hello_spatial.py` lives in `examples/`, not `samples/`: it is something a
reader runs, not something the gateway serves. `samples/` holds inputs to the
gateway and nothing else.

`lib/spatialdds/` is a separate distribution that happens to live in this
repository. It depends on `requests` and `websockets` and on nothing here —
`gates/library.py` imports it with this repository off `sys.path` to keep that
true — and the gateway never imports it in either direction. The duplicated
`serializer_tables.json` is deliberate: the package has to carry its own copy
to be installable, and the two are required to agree by the same gate.
