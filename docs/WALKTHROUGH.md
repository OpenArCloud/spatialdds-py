# A VPS and a duck, published and followed back

Two publishers, end to end, over the implementation-defined write surface
([PUBLISH.md](PUBLISH.md)): a localization service with a resolvable manifest,
and a piece of 3D content followed from `PUT` to fetched model bytes. Both land
*beside* the fountain recording's own records, not in place of them — the point
is published records showing up as siblings of recorded ones.

Every command below runs verbatim against the endpoint the serve line starts,
and `gates/quickstart.py` runs them in this order on every gate run.

**One note, which the 1.8 revision turned from an apology into an exhibit.**
Until `297c433` no SpatialDDS 1.8 anchor type could carry a content reference,
so this walkthrough's duck was placed by its own announce's position and
coverage and nothing pointed at it — the live demonstration of why 1.9
candidates entry 2 existed. The revision landed it: `GeoAnchor` now has
`manifest_uri`, and the fountain recording uses it, with the plaque anchor
naming the label's content manifest and the other four anchors naming nothing.

The duck published here is still placed by its own coverage, because this
endpoint publishes exactly two record kinds — `announce` and
`content_announce` — and anchors remain read-only on it. Publishing an anchor
that names the duck is the obvious next gesture and it needs the write surface
to accept a third kind, which is a separate batch. What the revision changed is
that the reference is now expressible and exercised; what it did not change is
which records this endpoint accepts.

The service manifest is derived from the specification's own
`manifests/v1.8/vps_manifest.json`. Deriving it surfaced two defects in that
example, both filed: see findings 27 and 28 in
[WEB-BINDING-FINDINGS.md](../WEB-BINDING-FINDINGS.md).

## Serve it

The write surface is off by default and operator-scoped when on, and records
are accepted only into the authority the endpoint answers for:

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 \
    --public-url https://demo.spatialdds.org --token tok --publish \
    --journal /tmp/registry.mcap
```

Every numbered block below sets the two variables it needs, so any one of them
can be pasted into a fresh shell without having run the others. The gateway
prints these same export lines at startup, with the real port and token.

## 1. The VPS announce, and the search that finds it

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service"]["record"]))' \
  | curl -s -X PUT "$BASE/ut-austin-south-mall/discovery/announce/v1/austin-vps" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("PUT    ", d["service_id"], "|", d["kind"], "|", d["manifest_uri"])'
curl -s "$BASE/.well-known/spatialdds/search" \
  | python3 -c 'import json,sys; print("GET     search ->", ", ".join(sorted(r["service"]["service_id"] for r in json.load(sys.stdin)["results"])))'
```

```console
PUT     austin-vps | VPS | spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps
GET     search -> austin-vps, fountain-anchors
```

### The same record, from the builders

Everything above went over the wire from a committed JSON file, 18 members written out by hand. This is the same record from `sd.records`, compared against what the endpoint just served: what a publisher writes is the arguments, and `caps` is a claim so it is one of them. So is the frame: this service works in a surveyed site frame rather than in WGS84, and `sd.frame` is how a publisher says that.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], scene="ut-austin-south-mall")
site = sd.frame("ut-austin/south-mall/map",
                uuid="b1f0c2a4-5d6e-4f80-9a1b-2c3d4e5f6071",
                convention="ENU", meters_per_unit=1.0)
area = sd.circle(30.28495359, -97.73934584, 25.0, alt_m=158.0,
                 frame_ref=site, frame_declared=False)
built = sd.service_announce(
    "austin-vps", "VPS", coverage=area, coverage_frame_ref=site,
    name="Austin VPS, published over HTTP", org="openarcloud.example",
    version="2026-q4",
    manifest_uri="spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps",
    caps=sd.capabilities(
        supported=[sd.profile("spatial.core", minor=8),
                   sd.profile("spatial.disco", minor=8)],
        features=["web-binding/json"]),
    topics=[sd.topic("geo_anchor", scene="ut-austin-south-mall",
                     stream="frames"),
            sd.topic("content_announce", scene="ut-austin-south-mall",
                     stream="content")],
    ttl_sec=86400)
served = ep.get_json("/ut-austin-south-mall/discovery/announce/v1/austin-vps")
diff = sorted(k for k in set(served) | set(built) if served[k] != built.get(k))
print("announce, conventions excepted:", diff or "same document")
PY
```

```console
announce, conventions excepted: ['stamp']
```

## 2. The VPS manifest, and the URI that now resolves

The announce names a `manifest_uri`; this is the manifest it names, published
into the same store the resolver reads.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service_manifest"]))' \
  | curl -s -X PUT "$BASE/spatialdds/resolve" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("PUT    ", d["rtype"], d["id"], "ttl", d["ttl_sec"])'
curl -s --get "$BASE/spatialdds/resolve" \
      --data-urlencode "uri=spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("GET     resolve ->", d.get("rtype"), "ttl", d.get("ttl_sec"))'
```

```console
PUT     service spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps ttl 1800
GET     resolve -> service ttl 1800
```

### The same record, from the builders

And the contract, which is where a service says what it actually does. Every member of it is a claim, so every member of it is an argument.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], scene="ut-austin-south-mall")
area = sd.circle(30.28495359, -97.73934584, 25.0, alt_m=158.0)
built = sd.service_manifest(
    "austin-vps", "VPS", authority="demo.spatialdds.org", scene="ut-austin-south-mall",
    name="Austin VPS, published over HTTP", org="openarcloud.example",
    version="2026-q4", profiles=["Core", "SLAM Frontend", "AR+Geo"],
    request={"features_topic": "feat.keyframe",
             "image_blob_role": "image/jpeg", "prior_topic": "geo.fix"},
    response={"rich": "pg.nodegeo", "minimal": "geo.fix"},
    limits={"max_fps": 10, "max_image_px": 1920},
    auth={"scheme": "bearer", "issuer": "https://demo.spatialdds.org"},
    coverage=area, cells=sd.cells(area), ttl_sec=1800)
served = ep.resolve(built["id"]).doc
diff = sorted(k for k in set(served) | set(built) if served[k] != built.get(k))
print("manifest, conventions excepted:", diff or "same document")
PY
```

```console
manifest, conventions excepted: ['stamp']
```

## 3. The duck's manifest

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
python3 -c 'import json; print(json.dumps(json.load(open("samples/duck.input.json"))["manifest"]))' \
  | curl -s -X PUT "$BASE/spatialdds/resolve" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d["assets"][0]; print("PUT    ", d["id"], "->", a["media_type"])'
curl -s --get "$BASE/spatialdds/resolve" \
      --data-urlencode "uri=spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck" \
  | python3 -c 'import json,sys; print("GET     resolve ->", json.load(sys.stdin)["assets"][0]["uri"])'
```

```console
PUT     spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck -> model/gltf+json
GET     resolve -> https://demo.spatialdds.org/assets/duck.gltf
```

### The same record, from the builders

The duck's manifest, with the asset hashed from the bytes in this checkout rather than from a digest anybody typed.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], scene="ut-austin-south-mall")
built = sd.content_manifest(
    "content/duck", authority="demo.spatialdds.org", scene="ut-austin-south-mall",
    title="A duck at the Littlefield Fountain",
    summary="An original glTF duck, placed by its own pose beside the fountain.",
    tags=["duck", "glTF", "walkthrough"],
    class_id="https://schema.org/CreativeWork",
    assets=[sd.asset("samples/assets/duck.gltf",
                     media_type="model/gltf+json",
                     uri="https://demo.spatialdds.org/assets/duck.gltf",
                     committed_path="samples/assets/duck.gltf")],
    coverage=sd.circle(30.2849859, -97.73935, 2.0, alt_m=158.0),
    ttl_sec=3600)
served = ep.resolve(built["id"]).doc
diff = sorted(k for k in set(served) | set(built) if served[k] != built.get(k))
print("manifest, conventions excepted:", diff or "same document")
PY
```

```console
manifest, conventions excepted: ['stamp']
```

## 4. The duck's announce, and the siblings it joins

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
python3 -c 'import json; print(json.dumps(json.load(open("samples/duck.input.json"))["announce"]["record"]))' \
  | curl -s -X PUT "$BASE/ut-austin-south-mall/content/content_announce/v1/content%2Fduck" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d["coverage"][0]; print("PUT    ", d["content_id"], "at", c["circle_center"][1], c["circle_center"][0], "r", c["circle_radius_m"])'
curl -s "$BASE/ut-austin-south-mall/content/content_announce/v1" \
  | python3 -c 'import json,sys; print("GET     list ->", ", ".join(sorted(c["content_id"] for c in json.load(sys.stdin))))'
```

```console
PUT     content/duck at 30.2849859 -97.73935 r 2.0
GET     list -> content/duck, content/fountain-label, content/fountain-marker
```

### The same record, from the builders

And the announce that places it. `provider_id` is here because the committed record states one: the builders never fill identity, so if a publisher wants to be named they name themselves.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], scene="ut-austin-south-mall")
site = sd.frame("ut-austin/south-mall/map",
                uuid="b1f0c2a4-5d6e-4f80-9a1b-2c3d4e5f6071",
                convention="ENU", meters_per_unit=1.0)
built = sd.content_announce(
    "content/duck", coverage=sd.circle(30.2849859, -97.73935, 2.0,
                                       alt_m=158.0, frame_ref=site,
                                       frame_declared=False),
    coverage_frame_ref=site,
    title="A duck at the Littlefield Fountain",
    summary="An original glTF duck, placed by its own pose beside the fountain.",
    tags=["duck", "glTF", "walkthrough"],
    class_id="https://schema.org/CreativeWork",
    provider_id="openarcloud.example/austin",
    manifest_uri="spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck",
    available_from="2026-10-09T15:30:00Z", ttl_sec=86400)
served = ep.get_json(
    "/ut-austin-south-mall/content/content_announce/v1/content%2Fduck")
diff = sorted(k for k in set(served) | set(built) if served[k] != built.get(k))
print("announce, conventions excepted:", diff or "same document")
PY
```

```console
announce, conventions excepted: ['stamp']
```

## 4b. Anchor the duck, and see what it inherits

The 1.8 revision gave `GeoAnchor` a `manifest_uri`, so the duck can be anchored
rather than only placed. Two PUTs did the content; this is the anchor that
names it.

**Method is the claim level.** This anchor says `VisualFix`, with a covariance
of about ±0.70 m at worst and a `confidence` that is the monotone summary of
it. Content named by an anchor inherits the anchor's frame and precision, so
the duck is now located to that standard and no better. Publishing it did not
make it surveyed.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
python3 -c 'import json; print(json.dumps(json.load(open("samples/duck.input.json"))["anchor"]["record"]))' \
  | curl -s -X PUT "$BASE/ut-austin-south-mall/frames/geo_anchor/v1/anchor%2Fduck-perch" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("PUT    ", d["anchor_id"], "|", d["method"], "| conf", d["confidence"], "|", d["manifest_uri"])'
curl -s "$BASE/ut-austin-south-mall/frames/geo_anchor/v1" \
  | python3 -c 'import json,sys; print("GET     anchors ->", ", ".join(sorted(a["anchor_id"] for a in json.load(sys.stdin))))'
```

```console
PUT     anchor/duck-perch | VisualFix | conf 0.5882 | spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck
GET     anchors -> anchor/duck-perch, anchor/fountain-east-rim, anchor/fountain-north-rim, anchor/fountain-plaque, anchor/fountain-south-rim, anchor/fountain-west-rim
```

The published anchor stands beside the recording's five. The plaque anchor
already named the label's manifest from the recording; this one names the
duck's from a `PUT`, which is the same member reached two ways.

## 5. Search finds the service

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
  | python3 -c 'import json,sys; print(", ".join(sorted(r["service"]["service_id"] for r in json.load(sys.stdin)["results"])))'
```

```console
austin-vps, fountain-anchors
```

## 6. The duck is a sibling, not a replacement

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
curl -s "$BASE/ut-austin-south-mall/content/content_announce/v1" \
  | python3 -c 'import json,sys; print(", ".join(sorted(c["content_id"] for c in json.load(sys.stdin))))'
```

```console
content/duck, content/fountain-label, content/fountain-marker
```

## 7. Resolve the URI, fetch the bytes

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
curl -s --get "$BASE/spatialdds/resolve" \
      --data-urlencode "uri=spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["assets"][0]["hash"])'
python3 -c 'import os, sys, urllib.request
b = urllib.request.urlopen(os.environ["BASE"] + "/assets/duck.gltf").read()
print(f"{len(b)} bytes") if b else sys.exit("  nothing came back")'
```

```console
sha256:8f73ee013981fc5c6ff4f0bb564b18a2a227fd25ba872f1f47bf9b87a3e488fe
1432 bytes
```

That last pair is the whole claim: a URI that did not exist before step 3
resolves to a manifest whose ref returns the committed asset's exact bytes.
`gates/publish_duck.py` byte-compares them against `samples/assets/duck.gltf`.

## 8. See what you published

The steps above each prove one hop in a line. This one shows the records
themselves — what a published VPS and a published duck actually look like on
the wire, and what a reader's client will receive. Three documents, printed in
full.

**The VPS manifest**, resolved through §7.5.2. Derived from the
specification's own `manifests/v1.8/vps_manifest.json`; the `profile` and `id`
it carries are the two things that example is missing (findings 27 and 28).

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s --get "$BASE/spatialdds/resolve" \
      --data-urlencode "uri=spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps" \
  | python3 -m json.tool
```

```console
{
    "auth": {
        "issuer": "https://demo.spatialdds.org",
        "scheme": "bearer"
    },
    "coverage": {
        "circle_center": [
            -97.73934584,
            30.28495359,
            158
        ],
        "circle_radius_m": 25,
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
    "id": "spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps",
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

**The duck's manifest.** `assets[0].hash` is the digest of the committed
`samples/assets/duck.gltf`, and `assets[0].meta` records the path it was
authored from, so the document says where its own bytes came from.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s --get "$BASE/spatialdds/resolve" \
      --data-urlencode "uri=spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck" \
  | python3 -m json.tool
```

```console
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
            158
        ],
        "circle_radius_m": 2,
        "frame_ref": {
            "fqn": "earth-fixed",
            "uuid": ""
        },
        "global": false,
        "has_bbox": false,
        "has_circle": true
    },
    "id": "spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/duck",
    "profile": "spatial.manifest/1.8",
    "rtype": "content",
    "stamp": {
        "nanosec": 0,
        "sec": "1791559800"
    },
    "ttl_sec": 3600
}
```

**The announce itself**, from the instance URL a `GET` serves — the same URL
the `PUT` in step 1 went to. This is the typed SpatialDDS record, canonical
JSON per N.1: `int64` members as decimal strings, enums by name, `has_*`
guards present. A published instance is indistinguishable from a replayed one,
and this is the form that holds for both.

Three things are worth noticing in it. Its `topics` entries now carry
`durability`: the 1.8 revision (`9c3e1f2`) gave `TopicMeta` the member, closing
finding 17 — before it, a bridged gateway had nowhere to read N.7's required
manifest `durability` from and would have had to invent one. The `url` on a
search result's topic entry is still something the manifest adds and the raw
record cannot, because `TopicMeta` has no URL field. And `manifest_uri` points
at the document printed first in this step: the announce names its manifest,
and the manifest resolves.

That `durability` now has two sources — this record and the manifest the
gateway derives — so `gates/anchor_serve.py` compares them per topic. Two
places that can disagree about one fact is a defect class this repository has
met before.

<!-- gate: publish sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s "$BASE/ut-austin-south-mall/discovery/announce/v1/austin-vps" \
  | python3 -m json.tool
```

```console
{
    "auth_hint": "",
    "caps": {
        "features": [
            "web-binding/json"
        ],
        "preferred_profiles": [],
        "supported_profiles": [
            {
                "major": 1,
                "max_minor": 8,
                "min_minor": 8,
                "name": "spatial.core"
            },
            {
                "major": 1,
                "max_minor": 8,
                "min_minor": 8,
                "name": "spatial.disco"
            }
        ]
    },
    "coverage": [
        {
            "aabb": {
                "max_xyz": [
                    0.0,
                    0.0,
                    0.0
                ],
                "min_xyz": [
                    0.0,
                    0.0,
                    0.0
                ]
            },
            "bbox": [
                0.0,
                0.0,
                0.0,
                0.0
            ],
            "circle_center": [
                -97.73934584,
                30.28495359,
                158.0
            ],
            "circle_radius_m": 25.0,
            "coverage_window_end": {
                "nanosec": 0,
                "sec": "0"
            },
            "coverage_window_start": {
                "nanosec": 0,
                "sec": "0"
            },
            "crs": "EPSG:4326",
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
            "global": false,
            "has_aabb": false,
            "has_bbox": false,
            "has_circle": true,
            "has_coverage_window": false,
            "has_crs": true,
            "has_frame_ref": false
        }
    ],
    "coverage_eval_time": {
        "nanosec": 0,
        "sec": "0"
    },
    "coverage_frame_ref": {
        "coord_convention": "ENU",
        "display_unit": "m",
        "fqn": "ut-austin/south-mall/map",
        "has_coord_convention": true,
        "has_scale": true,
        "meters_per_unit": 1.0,
        "scale_status": "SCALE_DECLARED",
        "uuid": "b1f0c2a4-5d6e-4f80-9a1b-2c3d4e5f6071"
    },
    "coverage_source_ids": [],
    "has_coverage_eval_time": false,
    "hints": [],
    "kind": "VPS",
    "manifest_uri": "spatialdds://demo.spatialdds.org/ut-austin-south-mall/service/austin-vps",
    "name": "Austin VPS, published over HTTP",
    "org": "openarcloud.example",
    "service_id": "austin-vps",
    "stamp": {
        "nanosec": 0,
        "sec": "1791559800"
    },
    "topics": [
        {
            "durability": "TRANSIENT_LOCAL",
            "max_chunk_bytes": 0,
            "name": "spatialdds/ut-austin-south-mall/frames/geo_anchor/v1",
            "qos_profile": "",
            "target_rate_hz": 0.0,
            "type": "geo_anchor",
            "version": "v1"
        },
        {
            "durability": "TRANSIENT_LOCAL",
            "max_chunk_bytes": 0,
            "name": "spatialdds/ut-austin-south-mall/content/content_announce/v1",
            "qos_profile": "",
            "target_rate_hz": 0.0,
            "type": "content_announce",
            "version": "v1"
        }
    ],
    "transforms": [],
    "ttl_sec": 86400,
    "version": "2026-q4"
}
```
