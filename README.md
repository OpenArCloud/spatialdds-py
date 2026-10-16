# spatialdds-web

SpatialDDS over plain HTTPS and a WebSocket: `fetch` and a `WebSocket` are the
whole client, with no DDS library, no domain, and no discovery handshake.
Running here are the reference registry, a client library, and a folder of
[examples](examples/) to paste into a terminal, a browser console, or a
ten-line Python file.

**No SDK between you and the data.** One `curl` asks what covers a geohash
cell and gets back the URL of everything there. Serve the sample locally --

```sh
pip install requests websockets aiohttp mcap
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787
```

-- and then, in a second terminal:

<!-- gate: fountain sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
    | python3 -c 'import json,sys; [print(t["type"], t["url"]) for t in json.load(sys.stdin)["results"][0]["service"]["topics"]]'
```

```console
content_announce http://127.0.0.1:8787/ut-austin-south-mall/content/content_announce/v1
announce http://127.0.0.1:8787/ut-austin-south-mall/discovery/announce/v1
geo_anchor http://127.0.0.1:8787/ut-austin-south-mall/frames/geo_anchor/v1
```

That is the floor, and it is the point: everything below is convenience, not
gatekeeping. Nothing here is reachable only through a library.

## Your own registry

A SpatialDDS registry, stood up and populated, with no clone and no recording
to find first. The types come from the specification pinned inside the
package, so an empty registry already knows what a `GeoAnchor` is.

```sh
pip install "spatialdds[server]"             # once 0.2.0 is on PyPI
pip install "./lib/spatialdds[server]"       # from this checkout, today
```

```sh
python -m spatialdds.serve --authority registry.mytown.example \
    --publish --token tok --port 8787
```

`--token tok` is here so that the next block can be pasted as it stands;
leave it out and a token is minted and printed instead, which is what a real
registry wants. The banner also names the journal it will write and the export
lines for a second terminal. Then a service and a piece of content, one call each:

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"])
ep.publish_service(service_id="mytown-vps", kind="VPS",
                   coverage=sd.circle(30.2849, -97.7393, 500))
ep.publish_content(content_id="duck", title="Duck",
                   position=(30.2849, -97.7393),
                   asset="https://cdn.example/duck.gltf")
ep.publish_anchor("anchor/duck-perch", lat=30.2849, lon=-97.7393,
                  method="VisualFix", sigma_enu_m=(0.25, 0.25, 0.5),
                  content="duck")
hit = ep.search(geohash="9v6krc")[0]
print("published", hit.service_id, hit.kind)
for t in hit.topics:
    print(" ", t["type"], len(hit.topic(t["type"]).latched()), "instance(s)")
print("resolves ", ep.resolve(
    "spatialdds://registry.mytown.example/default/content/duck").doc["id"])
PY
```

```console
published mytown-vps VPS
  content_announce 1 instance(s)
  announce 1 instance(s)
  geo_anchor 1 instance(s)
resolves  spatialdds://registry.mytown.example/default/content/duck
```

Eighteen members of an `Announce`, sixteen of a `ContentAnnounce` and nine of a
`GeoAnchor` were filled from those arguments. What the builders put in the
fields you did not mention is listed field by field --
`gates/builders.py --census` prints it -- because a record that validates and
says something you did not choose is worse than no builder at all.

Stop the registry and start it again on the same journal and everything is
still there: the records, both manifests, and the anchor. A registry's state
is an ordinary SpatialDDS recording, readable by anything that reads one.

The whole story, walked: [docs/MYTOWN.md](docs/MYTOWN.md).

## Reading somebody else's

Six lines, typed values, and the latched set arriving before the live stream.

```sh
pip install spatialdds                       # once 0.2.0 is on PyPI
pip install ./lib/spatialdds                 # from this checkout, today
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787
```

<!-- bar: read -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787")             # or https://demo.spatialdds.org
svc = ep.search(geohash="9v6krc")[0]
for ev in svc.topic("geo_anchor").subscribe(timeout=10):
    if isinstance(ev, sd.EndOfReplay): print("latched set complete:", ev.replayed); break
    print(" ", ev.key, ev.data["method"], ev.data["geopose"]["stamp"]["sec"])
```

```console
  anchor/fountain-north-rim Surveyed 1791559800
  anchor/fountain-south-rim VisualFix 1791559800
  anchor/fountain-east-rim VisualFix 1791559800
  anchor/fountain-west-rim VisualFix 1791559800
  anchor/fountain-plaque GNSS 1791559800
latched set complete: 5
```

`geopose.stamp.sec` is an `int` there, not `"1791559800"`: Appendix N.1 puts
every 64-bit integer on the wire as a decimal string, and undoing that is most
of what the library is for. [`lib/spatialdds`](lib/spatialdds/README.md) has
the rest.

[`examples/hello_spatial.py`](examples/hello_spatial.py) is the same flow with
no library at all -- `requests` and a raw WebSocket, ten lines -- kept as the
transparency exhibit. If the library ever seems to be doing something clever,
that file is the whole of it.

**One browser console.** Public resources are CORS-open, so this runs in
devtools on any page.

<!-- gate: fountain js-ok -->
```js
const BASE = "https://demo.spatialdds.org";
const svc = (await (await fetch(`${BASE}/.well-known/spatialdds/search`)).json()).results[0].service;
const anchors = await (await fetch(svc.topics.find(t => t.type === "geo_anchor").url)).json();
console.table(anchors.map(a => ({ id: a.anchor_id, lat: a.geopose.lat_deg, lon: a.geopose.lon_deg, method: a.method })));
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
export BASE=http://127.0.0.1:8787
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service"]["record"]))' \
    | curl -s -X PUT "$BASE/ut-austin-south-mall/discovery/announce/v1/austin-vps" \
        -H "Authorization: Bearer tok" -H "Content-Type: application/json" \
        --data @- \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("published", d["service_id"], "|", d["name"])'
```

```console
published austin-vps | Austin VPS, published over HTTP
```

[`publish_service.py`](examples/publish_service.py),
[`publish_content.py`](examples/publish_content.py) and
[`publish_duck.py`](examples/publish_duck.py) each do a whole round trip in one
file — publish, then read back through search, the resolver, and for the duck
the asset's bytes.

Two walked documents cover the same ground from both ends:
[docs/MYTOWN.md](docs/MYTOWN.md) stands a registry up and populates it with
the library in eight steps, and [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)
does a VPS and a duck with `curl` and no library at all. Both are executed by
`gates/quickstart.py` exactly as printed.

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
| [`publish_service.py`](examples/publish_service.py) | Publish a service announce and its manifest, then read both back. Needs `--publish`. |
| [`publish_content.py`](examples/publish_content.py) | Publish a content record and its manifest, then read both back. Needs `--publish`. |
| [`publish_duck.py`](examples/publish_duck.py) | Publish a duck, then resolve its URI and fetch the model bytes. Needs `--publish`. |
| [`publish_anchor.py`](examples/publish_anchor.py) | The open-anchors gesture in two PUTs: publish content, then an anchor that names it. Needs `--publish`. |

Every claim here is gated and conformance-tested: see
[docs/EVIDENCE.md](docs/EVIDENCE.md).
