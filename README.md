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

```sh
export BASE=https://demo.spatialdds.org          # coming online
export BASE=http://127.0.0.1:8787                # works today
```

**One curl.** Ask what covers a geohash cell; get back the URL of everything
there.

<!-- gate: fountain sh -->
```console
$ curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
    | python3 -c 'import json,sys; [print(t["type"], t["url"]) for t in json.load(sys.stdin)["results"][0]["service"]["topics"]]'
content_announce $BASE/ut-austin-south-mall/content/content_announce/v1
announce $BASE/ut-austin-south-mall/discovery/announce/v1
geo_anchor $BASE/ut-austin-south-mall/frames/geo_anchor/v1
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

<!-- gate: retail sh -->
```console
$ SPATIALDDS_ENDPOINT=$BASE python3 examples/hello_spatial.py
RESTRICTED       Keep-Out Zone        821266db-5c13-414b-91e0-7d2d4c2c307a
MONITORING       Queue Region         ac869071-2efa-47d2-bf77-fa0c7248593e
latched set complete: 2 zone(s)
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

## Examples

| | |
| --- | --- |
| [`curl.md`](examples/curl.md) | Search by geohash, list anchors, fetch a zone, read the headers. |
| [`browser.md`](examples/browser.md) | One `fetch` and a `console.table`; then a `WebSocket` to `end_of_replay`. |
| [`hello_spatial.py`](examples/hello_spatial.py) | The ten-line proof: discover, subscribe, read typed samples. |
| [`zones.py`](examples/zones.py) | Six lines: what zones are here. |
| [`anchors.py`](examples/anchors.py) | Anchors with their method and positional uncertainty. |
| [`migration/ssd_search.py`](examples/migration/ssd_search.py) | OSCP Spatial Service Discovery, and the search path that replaces it. |
| [`migration/scd_content.py`](examples/migration/scd_content.py) | OSCP Spatial Content Discovery: announce, resolved URI, manifest, asset. |

Every claim here is gated and conformance-tested: see
[docs/EVIDENCE.md](docs/EVIDENCE.md).
