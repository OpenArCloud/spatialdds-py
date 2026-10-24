# curl

Three questions, three one-liners. Nothing here is SpatialDDS-aware: it is `curl`
against `application/json`.

Every command block below sets the endpoint it uses, so any one of them can be
pasted into a fresh shell without having run the others. Against the demo
endpoint once it is online, the only change is
`export BASE=https://demo.spatialdds.org` in place of the loopback address.

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
export BASE=http://127.0.0.1:8787
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
    | python3 -c 'import json,sys; [print(r["service"]["service_id"], r["service"]["kind"], len(r["service"]["topics"]), "topics") for r in json.load(sys.stdin)["results"]]'
```

```console
fountain-anchors CONTENT 3 topics
```

## What anchors does it hold?

The manifest gave you the URL; the URL is a latched resource. One GET returns
every live instance. The last column is the 1.8 revision's `manifest_uri`: an
anchor may name a content manifest, and four of these five name nothing, which
is what an absent reference looks like on the wire — an empty string, no
`has_` guard.

<!-- gate: fountain sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s "$BASE/ut-austin-south-mall/frames/geo_anchor/v1" \
    | python3 -c 'import json,sys; [print("%-30s %.6f %.6f %-9s %s" % (a["anchor_id"], a["geopose"]["lat_deg"], a["geopose"]["lon_deg"], a["method"], a["manifest_uri"] or "-")) for a in json.load(sys.stdin)]'
```

```console
anchor/fountain-north-rim      30.284986 -97.739350 Surveyed  -
anchor/fountain-south-rim      30.284914 -97.739350 VisualFix -
anchor/fountain-east-rim       30.284950 -97.739314 VisualFix -
anchor/fountain-west-rim       30.284950 -97.739386 VisualFix -
anchor/fountain-plaque         30.284968 -97.739329 GNSS      spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/fountain-label
```

## What is the shape of a zone?

Zones live on the retail recording, so this half of the page needs the other
one served. Stop the first gateway and start this, which is the same line with
a different `--mcap` and port:

```sh
python3 tools/run_gateway.py --mcap samples/queuing-retail-sample.mcap --port 8788
```

That instruction used to be a sentence telling you to swap an argument, which
the gate could not execute and a reader had to act on. It is a command block
now, and `gates/quickstart.py` runs it as its own phase.

<!-- gate: retail sh -->
```sh
export BASE=http://127.0.0.1:8788
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
export BASE=http://127.0.0.1:8788
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
