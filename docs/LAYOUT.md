# Layout

Moved here intact from the README, and brought current with the directories the
storefront batch added.

See also [`WIRE.md`](WIRE.md) for the wire format and the decoding contract,
[`EVIDENCE.md`](EVIDENCE.md) for the gates, and [`RESOLUTION.md`](RESOLUTION.md)
for the `spatialdds://` resolution characterization.

## Layout

```
lib/spatialdds/   the distribution. Everything that ships:
  spatialdds/       client half -- client (the API), canonical (the lift),
                    errors, records (the builders), serve (the command)
  spatialdds/server/  gateway half -- idlmodel (wire model from IDL), xcdr2
                    (reader+writer), webjson (canonical JSON, both
                    directions), gateway (state, discovery), gw_http (the
                    pinned HTTP and WebSocket surface), publish (the write
                    surface), pin (the vendored type source)
  spatialdds/artifacts/  vendored and generated: 112 JSON Schemas, the
                    serializer tables, the pinned IDL
  spatialdds/demo/  the fountain set `--demo` serves, packed from samples/
webjson/          golden vectors, MAPPING, PROVENANCE. The generated
                  artifacts used to live here and now travel in the package;
                  this is what stayed behind, because it is about the
                  vendoring rather than part of it.
samples/          two recordings, their inputs, assets, content manifests,
                  and the authored records the publish gates check against
examples/         everything a reader runs: curl, browser console, eight
                  scripts, and the migration pair under examples/migration/
gates/            everything above
tools/            run_gateway.py (a shim over python -m spatialdds.serve),
                  author_anchors.py, pack_demo.py
docs/             WIRE, EVIDENCE, LAYOUT, RESOLUTION, PUBLISH,
                  WALKTHROUGH (by curl), MYTOWN (by library)
deploy/           Dockerfile and RUNBOOK for the hosted endpoint, unbuilt
lib/spatialdds/   the `spatialdds` client package: its own pyproject, its own
                  README, and a vendored copy of the serializer tables
.github/          one workflow, inert: trusted publishing for that package
```

`hello_spatial.py` lives in `examples/`, not `samples/`: it is something a
reader runs, not something the gateway serves. `samples/` holds inputs to the
gateway and nothing else.

`lib/spatialdds/` is the distribution, and since 0.2.0 it holds the gateway
too. That was the point: `pip install "spatialdds[server]"` and `python -m
spatialdds.serve` have to stand up a registry with no clone, and package data
has to live inside the package. So there is one gateway rather than one in the
repository and one in the wheel, and `tools/run_gateway.py` is a shim over it.

The two halves of the package are kept apart by `gates/import_graph.py`: the
client is `requests` and `websockets` talking to an endpoint, the server is
`aiohttp` and `mcap` being one, and neither imports the other. They share
`artifacts/` as data. The package's `__init__` stays free of third-party
imports so that importing either half costs nothing of the other -- it is a
lazy facade, because `import spatialdds.server` runs the parent first.

What is still duplicated, and why: `spatialdds/demo/` is a copy of the
fountain set in `samples/`. Package data cannot live outside the package, and
`samples/` is where the recording is authored. `tools/pack_demo.py` makes the
copy and `gates/serve_demo.py` fails if the two differ.
