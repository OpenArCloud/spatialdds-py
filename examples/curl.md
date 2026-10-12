# curl

Three questions, three one-liners. Nothing here is SpatialDDS-aware: it is `curl`
against `application/json`.

Set the endpoint once. The demo endpoint is coming online; the local command
beside it works today.

```sh
export BASE=http://127.0.0.1:8787
```

Once the demo endpoint is online it is the same commands with
`export BASE=https://demo.spatialdds.org` instead.

Serving locally takes two lines, run from the repository root and left running
while you work in a second terminal:

```sh
pip install aiohttp mcap
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787
```

## What is near me?

One cell of a geohash is the whole query. The endpoint answers with service
manifests, not with a bus you have to join.

<!-- gate: fountain sh -->
```sh
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
    | python3 -c 'import json,sys; [print(r["service"]["service_id"], r["service"]["kind"], len(r["service"]["topics"]), "topics") for r in json.load(sys.stdin)["results"]]'
```

```console
fountain-anchors CONTENT 3 topics
```

## What anchors does it hold?

The manifest gave you the URL; the URL is a latched resource. One GET returns
every live instance.

<!-- gate: fountain sh -->
```sh
curl -s "$BASE/ut-austin-south-mall/frames/geo_anchor/v1" \
    | python3 -c 'import json,sys; [print("%-30s %.6f %.6f %s" % (a["anchor_id"], a["geopose"]["lat_deg"], a["geopose"]["lon_deg"], a["method"])) for a in json.load(sys.stdin)]'
```

```console
anchor/fountain-north-rim      30.284986 -97.739350 Surveyed
anchor/fountain-south-rim      30.284914 -97.739350 VisualFix
anchor/fountain-east-rim       30.284950 -97.739314 VisualFix
anchor/fountain-west-rim       30.284950 -97.739386 VisualFix
anchor/fountain-plaque         30.284968 -97.739329 GNSS
```

## What is the shape of a zone?

Zones live on the retail recording. Serve it the same way, swapping the `--mcap`
argument for `samples/queuing-retail-sample.mcap`.

<!-- gate: retail sh -->
```sh
curl -s "$BASE/302cf49a-97ec-402d-a324-c5077b280b7b/regions/spatial_zone/v1" \
    | python3 -c 'import json,sys; [print(z["kind"], z["name"], "|", len(z["polygon"]), "vertices", z["z_min"], "to", z["z_max"], "m") for z in json.load(sys.stdin)]'
```

```console
RESTRICTED Keep-Out Zone | 4 vertices 0.0 to 2.0 m
MONITORING Queue Region | 4 vertices 0.0 to 2.0 m
```

## What the headers tell you

Every latched resource names its topic, its IDL type, its access class, and a
content ETag. A conditional GET on that ETag is a 304. This is what makes the
binding cacheable by ordinary infrastructure.

<!-- gate: retail sh -->
```sh
curl -sD - -o /dev/null "$BASE/302cf49a-97ec-402d-a324-c5077b280b7b/regions/spatial_zone/v1" \
    | tr -d '\r' | grep -iE '^(cache-control|x-spatialdds)'
```

```console
Cache-Control: public, max-age=300
X-SpatialDDS-Topic: spatialdds/302cf49a-97ec-402d-a324-c5077b280b7b/regions/spatial_zone/v1
X-SpatialDDS-Type: spatial.events.SpatialZone
X-SpatialDDS-Access: public-cacheable
```

Operator-scoped resources answer differently: `no-store`, and `401` without a
bearer token. See [docs/EVIDENCE.md](../docs/EVIDENCE.md) for the gate that
tests both directions.
