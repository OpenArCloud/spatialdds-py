# Examples

Seven examples, none longer than fifteen lines of code, all against a live
endpoint over plain HTTP. Every output printed below is the real output, checked
on every run by [`gates/examples.py`](../gates/examples.py): if an example rots,
a gate goes red rather than a reader going in circles.

| Example | What it shows |
| --- | --- |
| [`curl.md`](curl.md) | Search by geohash, list anchors, fetch a zone, read the headers. No client at all. |
| [`browser.md`](browser.md) | One `fetch` and a `console.table`; then a `WebSocket` read to `end_of_replay`. |
| [`hello_spatial.py`](hello_spatial.py) | The transparency exhibit: the library's flow done raw, in ten lines. Fountain. |
| [`zones.py`](zones.py) | Six lines: what zones are here. Retail. |
| [`anchors.py`](anchors.py) | Anchors with their method and their positional uncertainty. |
| [`migration/ssd_search.py`](migration/ssd_search.py) | OSCP Spatial Service Discovery and the search path that replaces it. |
| [`migration/scd_content.py`](migration/scd_content.py) | OSCP Spatial Content Discovery: announce, §7.5.2-resolved URI, manifest, asset. Fountain, served as its authority. |
| [`publish_service.py`](publish_service.py) | The SSD successor in the write direction: PUT an announce, then find it. Needs `--publish`. |
| [`publish_content.py`](publish_content.py) | The SCD successor in the write direction: PUT a content record and its manifest, then resolve it. Needs `--publish`. |
| [`publish_duck.py`](publish_duck.py) | Write to bytes in one file: publish a duck, resolve its URI, fetch the model. Needs `--publish`. |
| [`publish_anchor.py`](publish_anchor.py) | The open-anchors gesture in two PUTs: publish content, then an anchor naming it. Needs `--publish`. |

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
announce: that is `hello_spatial.py`, `anchors.py`, `curl.md`, `browser.md`,
and both migration examples -- everything the README's front page shows, which
is deliberate, because the front page serves exactly one recording and the
public endpoint serves that same one.
`queuing-retail-sample.mcap` carries zones, crossing lines, and live tracks:
that is `zones.py` alone. Serve both on two ports and everything here runs --
with `scd_content.py` wanting the fountain endpoint served as its authority,
per the `--public-url` line below.

The gate therefore stands up three endpoints, not two: the retail recording,
the fountain recording, and the fountain recording again as
`demo.spatialdds.org`. A marker's first word says which.

## hello_spatial.py

Ten lines, against **the fountain recording** -- the one the README's serve
line serves and the one the public endpoint serves. Only the base URL is
hardcoded; every other path is followed out of the service manifest rather than
constructed.

<!-- gate: fountain py examples/hello_spatial.py -->
```console
anchor/fountain-north-rim      Surveyed  30.284986, -97.739350
anchor/fountain-south-rim      VisualFix 30.284914, -97.739350
anchor/fountain-east-rim       VisualFix 30.284950, -97.739314
anchor/fountain-west-rim       VisualFix 30.284950, -97.739386
anchor/fountain-plaque         GNSS      30.284968, -97.739329
latched set complete: 5 anchor(s)
```

Point it at a recording with no anchors and it says so and stops, because a
newcomer's first thirty seconds should not end in a traceback:

<!-- gate: retail py-fails examples/hello_spatial.py -->
```console
$BASE serves no geo_anchor topic; it offers: crossing_line, detection2d, entity, fused_track, spatial_event, spatial_zone
```

## zones.py

**Wants the retail recording**, which is the only one here with zones in it. A
second recording off the front page is fine; an unlabelled one is not.

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

**The `spatialdds://` hop is real.** The example does what §7.5.1 orders: it
reads the authority's resolver metadata, follows the `https_base` advertised
there, and `GET`s the URI against it. Nothing is constructed from a path
segment. This replaced a fixture that mapped the URI to a committed file, and
[docs/RESOLUTION.md](../docs/RESOLUTION.md) records what the mechanism is and
which erratum made it conformant.

One substitution remains, and it is the reader's rather than the client's:
resolution answers only from the endpoint reachable at the DNS name its URIs
name, so a local copy of the authority is given its advertised paths against
`$SPATIALDDS_ENDPOINT`. Serve it as the authority to see the real shape:

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 \
    --public-url https://demo.spatialdds.org
```

**That serve line is for this example only.** It makes the endpoint advertise
absolute URLs under a name your machine cannot resolve, which is harmless for a
script that reuses only their paths and fatal for one that follows them as
given. Run the other examples, and the README's tiers, against the plain serve
line.

<!-- gate: authority py examples/migration/scd_content.py -->
```console
  Littlefield Fountain marker
    resolved spatialdds://demo.spatialdds.org/south-mall/content/fountain-marker
    model/gltf+json    https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf
    sha256:26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf
  Littlefield Fountain label
    resolved spatialdds://demo.spatialdds.org/south-mall/content/fountain-label
    text/plain         https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-label.txt
    sha256:1970b1b67efc916f9799db2a6159a713fa5a46bed0a0485df469bfcd857a669c
```

The chain ends where a client's does: an asset reference and the digest the
manifest declares for it, with the whole manifest printed so the reference is
read in context. Fetching those bytes and comparing them needs the
network and a checkout, so it is `gates/content_resolution.py --remote` rather
than an example.

## publish_service.py

The write direction of the SSD succession. The surface is an
implementation-defined extension, off unless the endpoint was started with
`--publish`, and operator-scoped when on -- so the token comes from the
environment and never from a URL. Serve it as its own authority, because a
record is accepted only into the authority the endpoint answers for:

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 \
    --public-url https://demo.spatialdds.org --token tok --publish \
    --journal /tmp/registry.mcap
```

<!-- gate: publish py examples/publish_service.py -->
```console
  PUT announce  -> 201  austin-vps
  PUT manifest  -> 201  spatialdds://demo.spatialdds.org/south-mall/service/austin-vps
  GET search    -> austin-vps, fountain-anchors
  GET resolve   -> service spatialdds://demo.spatialdds.org/south-mall/service/austin-vps ttl 1800

{
  "auth": {
    "issuer": "https://demo.spatialdds.org",
    "scheme": "bearer"
  },
  "coverage": {
    "circle_center": [
      -97.73934584,
      30.28495359,
      158.0
    ],
    "circle_radius_m": 25.0,
    "frame_ref": {
      "fqn": "earth-fixed",
      "uuid": ""
    },
    "geohash": [
      "9v6krc"
    ],
    "global": false,
    "has_bbox": false,
    "has_circle": true
  },
  "id": "spatialdds://demo.spatialdds.org/south-mall/service/austin-vps",
  "limits": {
    "max_fps": 10,
    "max_image_px": 1920
  },
  "profile": "spatial.manifest/1.8",
  "profiles": [
    "Core",
    "SLAM Frontend",
    "AR+Geo"
  ],
  "request": {
    "features_topic": "feat.keyframe",
    "image_blob_role": "image/jpeg",
    "prior_topic": "geo.fix"
  },
  "response": {
    "minimal": "geo.fix",
    "rich": "pg.nodegeo"
  },
  "rtype": "service",
  "service": {
    "kind": "VPS",
    "name": "Austin VPS, published over HTTP",
    "org": "openarcloud.example",
    "service_id": "austin-vps",
    "version": "2026-q4"
  },
  "stamp": {
    "nanosec": 0,
    "sec": "1791559800"
  },
  "ttl_sec": 1800
}
```

Each line proves one hop; the dump at the end is the payoff. A one-line proof
tells you it worked and hides what you published, so the script finishes by
printing the manifest the resolver returned — not the body it sent, which would
only prove the script can echo itself.

## publish_content.py

A content record needs both halves -- the announce that says where the content
is, and the manifest its `manifest_uri` points at. The last line is the
resolver answering for a URI that did not exist a moment earlier, with the
`ttl_sec` rule applying to a published manifest exactly as to a committed one.

<!-- gate: publish py examples/publish_content.py -->
```console
  PUT announce  -> 201  content/south-mall-tour
  PUT manifest  -> 201  spatialdds://demo.spatialdds.org/south-mall/content/south-mall-tour
  GET list      -> content/fountain-label, content/fountain-marker, content/south-mall-tour
  GET resolve   -> content spatialdds://demo.spatialdds.org/south-mall/content/south-mall-tour ttl 7200 -> public, max-age=7200

{
  "assets": [
    {
      "hash": "sha256:abababababababababababababababababababababababababababababababab",
      "media_type": "application/json",
      "uri": "https://example.org/south-mall-tour.json"
    }
  ],
  "content": {
    "content_id": "content/south-mall-tour",
    "summary": "A short audio tour published over the web binding.",
    "title": "South Mall walking tour"
  },
  "id": "spatialdds://demo.spatialdds.org/south-mall/content/south-mall-tour",
  "profile": "spatial.manifest/1.8",
  "rtype": "content",
  "stamp": {
    "nanosec": 0,
    "sec": "1791559800"
  },
  "ttl_sec": 7200
}
```

Point either at an endpoint without `--publish` and it says so and stops:

<!-- gate: fountain py-fails examples/publish_service.py -->
```console
  refused: announce 405, manifest 405. 405 means no --publish, 401 a wrong token
```

## publish_duck.py

The one example that runs the whole chain in a single file: `PUT` the content
manifest, `PUT` the announce that places it, then back out through the read
path — the content collection, the resolved URI, and the asset's ref fetched,
so the last line is a byte count rather than a promise.

The duck is placed by its own announce's position and coverage. Nothing points
at it here, but not because nothing could any more: the 1.8 revision gave
`GeoAnchor` a `manifest_uri` and the fountain recording uses it. Anchors stay
read-only on this endpoint, so pinning the duck to one is a separate batch. See
[docs/WALKTHROUGH.md](../docs/WALKTHROUGH.md).

<!-- gate: publish py examples/publish_duck.py -->
```console
  PUT manifest  -> 201  spatialdds://demo.spatialdds.org/south-mall/content/duck
  PUT announce  -> 201  content/duck
  GET list      -> content/duck, content/fountain-label, content/fountain-marker
  GET resolve   -> model/gltf+json https://demo.spatialdds.org/assets/duck.gltf
  GET asset     -> 1432 bytes, hash matches: True

{
  "assets": [
    {
      "hash": "sha256:8f73ee013981fc5c6ff4f0bb564b18a2a227fd25ba872f1f47bf9b87a3e488fe",
      "media_type": "model/gltf+json",
      "meta": [
        {
          "entries": [
            {
              "key": "committed_path",
              "value": "samples/assets/duck.gltf"
            },
            {
              "key": "bytes",
              "value": "1432"
            }
          ],
          "json": "",
          "namespace": "spatialdds-web"
        }
      ],
      "uri": "https://demo.spatialdds.org/assets/duck.gltf"
    }
  ],
  "content": {
    "class_id": "https://schema.org/CreativeWork",
    "content_id": "content/duck",
    "summary": "An original glTF duck, placed by its own pose beside the fountain.",
    "tags": [
      "duck",
      "glTF",
      "walkthrough"
    ],
    "title": "A duck at the Littlefield Fountain"
  },
  "coverage": {
    "circle_center": [
      -97.73935,
      30.2849859,
      158.0
    ],
    "circle_radius_m": 2.0,
    "frame_ref": {
      "fqn": "earth-fixed",
      "uuid": ""
    },
    "global": false,
    "has_bbox": false,
    "has_circle": true
  },
  "id": "spatialdds://demo.spatialdds.org/south-mall/content/duck",
  "profile": "spatial.manifest/1.8",
  "rtype": "content",
  "stamp": {
    "nanosec": 0,
    "sec": "1791559800"
  },
  "ttl_sec": 3600
}
```

The `GET list` line is the walkthrough's point in miniature: the duck arrives
beside the recording's own two content records rather than in place of them.
And the dump is what a client receives — `assets[0].hash` is the digest of the
committed `samples/assets/duck.gltf`, and `assets[0].meta` records the path it
was authored from, so the document says where its own bytes came from.

## publish_anchor.py

The open-anchors gesture in two PUTs: publish the duck's manifest, then publish
an anchor whose `manifest_uri` names it. The 1.8 revision made the reference
expressible; this is what using it looks like from the write side.

**Method is the claim level.** The anchor says `VisualFix` with a covariance of
about ±0.70 m at worst, and that is what a reader inherits — content named by
it inherits the anchor's frame and precision. Publishing does not turn a visual
fix into surveyed truth, and `gates/publish_anchor.py` checks that
`confidence` is the monotone summary of `cov` that `GeoAnchor` asks for, so a
record this endpoint accepted cannot contradict itself.

<!-- gate: publish py examples/publish_anchor.py -->
```console
  PUT manifest  -> 201  spatialdds://demo.spatialdds.org/south-mall/content/duck
  PUT anchor    -> 201  anchor/duck-perch  method VisualFix
  GET anchors   -> anchor/duck-perch, anchor/fountain-east-rim, anchor/fountain-north-rim, anchor/fountain-plaque, anchor/fountain-south-rim, anchor/fountain-west-rim
  GET resolve   -> model/gltf+json https://demo.spatialdds.org/assets/duck.gltf

{
  "anchor_id": "anchor/duck-perch",
  "checksum": "sha256:3845ad6403ca9b8bd353ae33f3c039f19523a88da68c2d13e863e8d374c4b522",
  "confidence": 0.5882,
  "cov": {
    "pos": [
      0.16000000000000003,
      0.0,
      0.0,
      0.0,
      0.16000000000000003,
      0.0,
      0.0,
      0.0,
      0.48999999999999994
    ],
    "type": "COV_POS3"
  },
  "frame_ref": {
    "coord_convention": "ENU",
    "display_unit": "m",
    "fqn": "ut-austin/south-mall/map",
    "has_coord_convention": true,
    "has_scale": true,
    "meters_per_unit": 1.0,
    "scale_status": "SCALE_DECLARED",
    "uuid": "b1f0c2a4-5d6e-4f80-9a1b-2c3d4e5f6071"
  },
  "geopose": {
    "alt_m": 158.0,
    "cov": {
      "pos": [
        0.16000000000000003,
        0.0,
        0.0,
        0.0,
        0.16000000000000003,
        0.0,
        0.0,
        0.0,
        0.48999999999999994
      ],
      "type": "COV_POS3"
    },
    "lat_deg": 30.284993,
    "lon_deg": -97.739301,
    "q": [
      0.0,
      0.0,
      0.0,
      1.0
    ],
    "stamp": {
      "nanosec": 0,
      "sec": "1791559800"
    }
  },
  "manifest_uri": "spatialdds://demo.spatialdds.org/south-mall/content/duck",
  "map_id": "ut-austin/south-mall",
  "method": "VisualFix"
}
```

The third line is the point: the published anchor stands beside the recording's
five rather than replacing any of them, and the one it names resolves.

## How the gate reads this file

Markers like `<!-- gate: fountain py examples/zones.py -->` are not decoration:
`gates/examples.py` parses them, serves the named configuration -- `retail`,
`fountain`, `authority`, or `publish` -- runs the example, and diffs its stdout against
the fence that follows. `$BASE` in an expected
output stands for the endpoint the gate served it from, which is the one thing
that legitimately varies between a reader's run and the gate's.
