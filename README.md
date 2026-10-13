# spatialdds-web

SpatialDDS over plain HTTPS and a WebSocket: `fetch` and a `WebSocket` are the
whole client, with no DDS library, no domain, and no discovery handshake.
Running here are a reference gateway that serves a recorded SpatialDDS stream
and a folder of [examples](examples/) to paste into a terminal, a browser
console, or a ten-line Python file.

## Try it, smallest first

The demo endpoint is coming online; the local one works today. Serving it
locally is two lines, and either way the commands below are the same:

```sh
pip install requests websockets aiohttp mcap
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787
```

That command keeps running, so the rest goes in a second terminal. Every block
below is run from the repository root, and every block is copy-safe: a command
block holds only commands, and the output beside it is what you should see.

```sh
export BASE=http://127.0.0.1:8787
```

The demo endpoint, once it is online, is the same commands with
`export BASE=https://demo.spatialdds.org` instead.

**One curl.** Ask what covers a geohash cell; get back the URL of everything
there.

<!-- gate: fountain sh -->
```sh
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
    | python3 -c 'import json,sys; [print(t["type"], t["url"]) for t in json.load(sys.stdin)["results"][0]["service"]["topics"]]'
```

```console
content_announce http://127.0.0.1:8787/ut-austin-south-mall/content/content_announce/v1
announce http://127.0.0.1:8787/ut-austin-south-mall/discovery/announce/v1
geo_anchor http://127.0.0.1:8787/ut-austin-south-mall/frames/geo_anchor/v1
```

**One browser console.** Public resources are CORS-open, so this runs in
devtools on any page.

<!-- gate: fountain js-ok -->
```js
const BASE = "https://demo.spatialdds.org";
const svc = (await (await fetch(`${BASE}/.well-known/spatialdds/search`)).json()).results[0].service;
const anchors = await (await fetch(svc.topics.find(t => t.type === "geo_anchor").url)).json();
console.table(anchors.map(a => ({ id: a.anchor_id, lat: a.geopose.lat_deg, lon: a.geopose.lon_deg, method: a.method })));
```

**Ten lines of Python.** Discover a service, subscribe to one of its topics,
read typed samples until the latched set is complete.

<!-- gate: fountain sh -->
```sh
SPATIALDDS_ENDPOINT=$BASE python3 examples/hello_spatial.py
```

```console
anchor/fountain-north-rim      Surveyed  30.284986, -97.739350
anchor/fountain-south-rim      VisualFix 30.284914, -97.739350
anchor/fountain-east-rim       VisualFix 30.284950, -97.739314
anchor/fountain-west-rim       VisualFix 30.284950, -97.739386
anchor/fountain-plaque         GNSS      30.284968, -97.739329
latched set complete: 5 anchor(s)
```

## What's on the wire

| | |
| --- | --- |
| `GET /.well-known/spatialdds/search?geohash={cell}` | service manifests covering a cell |
| `GET /.well-known/spatialdds/schemas` | the schema index, one entry per type |
| `GET /.well-known/spatialdds/schemas/{type}` | one JSON Schema |
| `GET /.well-known/spatialdds/resolver` | resolver metadata, where the endpoint is an authority |
| `GET /spatialdds/resolve?uri={urlencoded}` | one `spatialdds://` URI, resolved to its manifest |
| `GET /{domain}/frames/geo_anchor/v1` | anchors |
| `GET /{domain}/regions/spatial_zone/v1` | zones |
| `GET /{domain}/content/content_announce/v1` | content |
| `GET /{domain}/{stream}/{type}/{version}` | any topic's latched set |
| `GET /{domain}/{stream}/{type}/{version}/{key}` | one instance, by its key |
| `WS /{domain}/{stream}/{type}/{version}` | the same URL, upgraded: replay, then live |

## Publish

Services and content can be published over HTTP: `PUT` on the same instance URL
a `GET` serves, `DELETE` to withdraw. **The surface is implementation-defined
and off by default** -- 1.8 specifies no publish verb and forbids none, so this
is an endpoint extension, not a reading of the specification. A read-only client
cannot tell a published record from a replayed one, which is the property that
keeps the endpoint conformant either way. See
[docs/PUBLISH.md](docs/PUBLISH.md).

Serve it with the surface on, as its own authority, and it needs a token:

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 \
    --public-url https://demo.spatialdds.org --token tok --publish \
    --journal /tmp/registry.mcap
```

Then one PUT, and the record is in the registry the read path serves:

<!-- gate: publish sh -->
```sh
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service"]["record"]))' \
    | curl -s -X PUT "$BASE/ut-austin-south-mall/discovery/announce/v1/austin-vps" \
        -H "Authorization: Bearer tok" -H "Content-Type: application/json" \
        --data @- \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("published", d["service_id"], "|", d["name"])'
```

```console
published austin-vps | Austin VPS, published over HTTP
```

[`publish_service.py`](examples/publish_service.py) and
[`publish_content.py`](examples/publish_content.py) do the same in thirteen
lines each, and read the record back out through search and the resolver.

## Examples

| | |
| --- | --- |
| [`curl.md`](examples/curl.md) | Search by geohash, list anchors, fetch a zone, read the headers. |
| [`browser.md`](examples/browser.md) | One `fetch` and a `console.table`; then a `WebSocket` to `end_of_replay`. |
| [`hello_spatial.py`](examples/hello_spatial.py) | The ten-line proof: discover, subscribe, read typed samples. |
| [`zones.py`](examples/zones.py) | Six lines: what zones are here. Wants the retail recording. |
| [`anchors.py`](examples/anchors.py) | Anchors with their method and positional uncertainty. |
| [`migration/ssd_search.py`](examples/migration/ssd_search.py) | OSCP Spatial Service Discovery, and the search path that replaces it. |
| [`migration/scd_content.py`](examples/migration/scd_content.py) | OSCP Spatial Content Discovery: announce, resolved URI, manifest, asset. Wants the endpoint served as its own authority. |
| [`publish_service.py`](examples/publish_service.py) | Publish a service announce, then find it. Needs `--publish`. |
| [`publish_content.py`](examples/publish_content.py) | Publish a content record and its manifest, then resolve it. Needs `--publish`. |

Every claim here is gated and conformance-tested: see
[docs/EVIDENCE.md](docs/EVIDENCE.md).
