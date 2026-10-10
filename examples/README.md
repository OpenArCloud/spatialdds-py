# Examples

Seven examples, none longer than fifteen lines of code, all against a live
endpoint over plain HTTP. Every output printed below is the real output, checked
on every run by [`gates/examples.py`](../gates/examples.py): if an example rots,
a gate goes red rather than a reader going in circles.

| Example | What it shows |
| --- | --- |
| [`curl.md`](curl.md) | Search by geohash, list anchors, fetch a zone, read the headers. No client at all. |
| [`browser.md`](browser.md) | One `fetch` and a `console.table`; then a `WebSocket` read to `end_of_replay`. |
| [`hello_spatial.py`](hello_spatial.py) | The ten-line proof: discover, subscribe, read typed samples. |
| [`zones.py`](zones.py) | Six lines: what zones are here. |
| [`anchors.py`](anchors.py) | Anchors with their method and their positional uncertainty. |
| [`migration/ssd_search.py`](migration/ssd_search.py) | OSCP Spatial Service Discovery and the search path that replaces it. |
| [`migration/scd_content.py`](migration/scd_content.py) | OSCP Spatial Content Discovery: announce to manifest to fetched asset. |

## The endpoint

Every script reads one environment variable, `SPATIALDDS_ENDPOINT`, and defaults
to the demo endpoint. The browser snippets have no environment, so they carry the
URL on their first line.

```sh
export SPATIALDDS_ENDPOINT=https://demo.spatialdds.org   # coming online
export SPATIALDDS_ENDPOINT=http://127.0.0.1:8787         # works today
```

Serving locally takes two lines, and which recording you serve decides which
examples have anything to say:

```sh
pip install requests websockets aiohttp mcap
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787
```

`fountain-anchors.mcap` carries anchors, content announces, and a service
announce: that is `anchors.py`, `curl.md`, `browser.md`, and both migration
examples. `queuing-retail-sample.mcap` carries zones, crossing lines, and live
tracks: that is `zones.py` and `hello_spatial.py`. Serve both on two ports and
everything here runs.

## hello_spatial.py

Ten lines, against the retail recording. Only the base URL is hardcoded; every
other path is followed out of the service manifest rather than constructed.

<!-- gate: retail py examples/hello_spatial.py -->
```console
RESTRICTED       Keep-Out Zone        821266db-5c13-414b-91e0-7d2d4c2c307a
MONITORING       Queue Region         ac869071-2efa-47d2-bf77-fa0c7248593e
latched set complete: 2 zone(s)
```

## zones.py

<!-- gate: retail py examples/zones.py -->
```console
2 zone(s) at SpatialDDS web gateway, domain 302cf49a-97ec-402d-a324-c5077b280b7b
  RESTRICTED       Keep-Out Zone        821266db-5c13-414b-91e0-7d2d4c2c307a
  MONITORING       Queue Region         ac869071-2efa-47d2-bf77-fa0c7248593e
```

## anchors.py

The uncertainty column is the largest standard deviation on the diagonal of the
position covariance, which is what `confidence` summarises into one number. A
surveyed anchor and a GNSS fix differ by an order of magnitude and the record
says so.

<!-- gate: fountain py examples/anchors.py -->
```console
  anchor/fountain-north-rim      30.284986,-97.739350 Surveyed  +/-0.09 m  conf 0.9174
  anchor/fountain-south-rim      30.284914,-97.739350 VisualFix +/-0.35 m  conf 0.7407
  anchor/fountain-east-rim       30.284950,-97.739314 VisualFix +/-0.31 m  conf 0.7634
  anchor/fountain-west-rim       30.284950,-97.739386 VisualFix +/-0.33 m  conf 0.7519
  anchor/fountain-plaque         30.284968,-97.739329 GNSS      +/-0.90 m  conf 0.5263
```

## migration/ssd_search.py

OSCP's Spatial Service Discovery answered "what services cover this cell". The
web binding answers the same question at a well-known path, and returns typed
service manifests instead of Spatial Service Records. The client still sends a
cell rather than a position, so the privacy posture is unchanged.

<!-- gate: fountain py examples/migration/ssd_search.py -->
```console
  was:  GET /ssrs?h3Index=8a2a1072b59ffff      -> Spatial Service Records
  now:  GET /.well-known/spatialdds/search?geohash=9v6krc

  fountain-anchors  kind=CONTENT  org=openarcloud.example
    content_announce   TRANSIENT_LOCAL  public-cacheable  $BASE/ut-austin-south-mall/content/content_announce/v1
    announce           TRANSIENT_LOCAL  public-cacheable  $BASE/ut-austin-south-mall/discovery/announce/v1
    geo_anchor         TRANSIENT_LOCAL  public-cacheable  $BASE/ut-austin-south-mall/frames/geo_anchor/v1
```

## migration/scd_content.py

OSCP's Spatial Content Discovery returned Spatial Content Records: a GeoPose plus
content references. `ContentAnnounce` carries the same thing with a
`manifest_uri`, and the manifest's envelope carries the assets with their
hashes — so the last line is not "it fetched" but "the bytes are the bytes that
were announced".

**The `spatialdds://` hop is a fixture, not a convention.** This endpoint
publishes no resolver, so the example maps the URI to a committed manifest by its
last path segment. The resolution mechanism is a pending design decision
([docs/RESOLUTION.md](../docs/RESOLUTION.md)); nothing in that mapping is a rule
a client may infer, and this example loses its fixture the day the mechanism
lands.

<!-- gate: fountain py examples/migration/scd_content.py -->
```console
  Littlefield Fountain marker
    spatialdds://austin.openarcloud.example/south-mall/content/fountain-marker
    model/gltf+json    https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf
    hash matches committed bytes: True
  Littlefield Fountain label
    spatialdds://austin.openarcloud.example/south-mall/content/fountain-label
    text/plain         https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-label.txt
    hash matches committed bytes: True
```

## How the gate reads this file

Markers like `<!-- gate: fountain py examples/zones.py -->` are not decoration:
`gates/examples.py` parses them, serves the named recording, runs the example,
and diffs its stdout against the fence that follows. `$BASE` in an expected
output stands for the endpoint the gate served it from, which is the one thing
that legitimately varies between a reader's run and the gate's.
