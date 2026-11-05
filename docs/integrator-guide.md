# Integrator guide

This guide takes you from nothing to reading, watching and publishing on a
SpatialDDS registry over plain HTTPS and WebSocket: content, live entities, and
claims about them. It is written against the public sandbox,
`https://demo.spatialdds.org`, which runs `spatialdds` 0.8.1 and speaks
SpatialDDS 1.8's web binding (Appendix N). Any registry that runs the same
package answers the same way; only the base URL and your token change.

Every command in this guide is run by this repository's own gate,
`gates/integrator_guide.py`, in document order, against a local registry
configured as the sandbox is, and its output is compared with the output
printed here. If the two ever disagree, the gate fails. The last section, "The
demo.spatialdds.org sandbox", is the sandbox's own contract: its quotas,
limits and lifecycle, which are its operator's choices rather than the
protocol's.

## Before you start

**A token and a lane.** Reading needs nothing. Writing needs a bearer token,
which comes with a lane: a scene name that your token, and only your token,
may write. To get one for the sandbox,
[contact Open AR Cloud](https://openarcloud.org/about/#contact). Keep the token
out of URLs and repositories; it can be rotated or revoked at any time.

**Tools.** The commands need `curl` and `python3` (3.9 or newer). The
WebSocket and Python sections also need the client library, which brings the
`websockets` package with it:

<!-- guide: install -->
```sh
pip install "spatialdds==0.8.1"
```

**Three variables.** Every command below uses them. Set them once in your
shell, with your own scene and token:

<!-- guide: setup -->
```sh
export BASE=https://demo.spatialdds.org
export SCENE='<your scene>'
export SPATIALDDS_TOKEN='<your token>'
```

The outputs below were printed with `SCENE=my-world` and a token whose
principal is `my-group`. Yours print your own names where those appear.

## Read in three calls

Reads need no token. Each call follows a link the previous one returned, so a
client never has to construct a path. The examples read the sandbox's sample
scene, `ut-austin-south-mall`; the same calls read yours.

**1. What is here?** Search by geohash. The answer is a list of service
manifests, each naming its topics and their URLs.

<!-- guide: sh -->
```sh
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
  | python3 -c 'import json,sys; [print(r["service"]["service_id"], [t["type"] for t in r["service"]["topics"]]) for r in json.load(sys.stdin)["results"]]'
```

```console
fountain-anchors ['content_announce', 'announce', 'geo_anchor']
```

A geohash search finds a scene by the coverage its service announce declares
(or, if it has none, by the area its geo anchors span), so it returns only the
scenes that cover that place. To make your lane findable where your content
is, see "Anywhere on Earth" below. A search with no geohash lists every scene.

**2. What content is placed here?** The content announces. This is a latched
collection: one JSON record per item, each carrying the `manifest_uri` that
describes it.

<!-- guide: sh -->
```sh
curl -s "$BASE/ut-austin-south-mall/content/content_announce/v1" \
  | python3 -c 'import json,sys; [print(c["content_id"], c["manifest_uri"]) for c in json.load(sys.stdin)]'
```

```console
content/fountain-marker spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/fountain-marker
content/fountain-label spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/fountain-label
```

**3. What is it?** Resolve a `spatialdds://` URI (§7.5.2) to its manifest.
Then fetch the asset it names and check it against the hash the manifest
declares.

<!-- guide: sh -->
```sh
curl -s "$BASE/spatialdds/resolve?uri=spatialdds%3A%2F%2Fdemo.spatialdds.org%2Fut-austin-south-mall%2Fcontent%2Ffountain-marker" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d["assets"][0]; print(d["content"]["title"]); print(a["uri"]); print(a["hash"])'
curl -s "https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf" | shasum -a 256
```

```console
Littlefield Fountain marker
https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf
sha256:26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf
26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf  -
```

The two digests match. An asset can live anywhere; this one is on GitHub, and
the manifest's hash is what makes that safe.

Two more read paths are worth knowing: the anchors, at
`$BASE/ut-austin-south-mall/frames/geo_anchor/v1`, and the published JSON
Schemas, at `$BASE/.well-known/spatialdds/schemas`. Latched collections carry
an `ETag` and answer `If-None-Match` with `304`. The full API reference is
generated from the running service, at
`$BASE/.well-known/spatialdds/openapi.html`.

## Watch your lane

Every collection URL is also a WebSocket. Connect with `wss://` and you
receive, in order: the latched set, as `sample` events; one `end_of_replay`
marker; then live `sample` and `dispose` events as they happen. Watching a
content or discovery lane needs no token; a world lane needs yours. Save this
as `watch.py`:

<!-- guide: file watch.py -->
```python
import json, os, sys
import websockets.sync.client as ws

lane = sys.argv[1]                      # e.g. content/content_announce/v1
url = os.environ["BASE"].replace("http", "ws", 1) + f"/{os.environ['SCENE']}/{lane}"
auth = {"Authorization": "Bearer " + os.environ["SPATIALDDS_TOKEN"]}
with ws.connect(url, additional_headers=auth if lane.startswith("world/") else {}) as c:
    while True:
        e = json.loads(c.recv())
        d = e.get("data", {})
        print(e["event"], e.get("key", ""), d.get("state", ""),
              repr(e.get("reason") or d.get("state_reason", "")), flush=True)
```

A lane exists from the first record published to it. Until then its URL
answers `404`, to a WebSocket as to a `GET`, so start the watcher once you
have published (step 2 of the next section).

## Publish your own object

Publishing content takes two `PUT`s: a **manifest**, which says what the
object is, where its bytes live, and their hash; and an **announce**, which
places it in your lane and points at the manifest. The manifest goes first,
so the announce never points at nothing.

**1. Write the two files.** The object sits three metres from the sandbox's
sample content only so that it lands beside it. Put your coordinates wherever
your object is.

<!-- guide: sh quiet -->
```sh
cat > manifest.json <<EOF
{
  "id": "spatialdds://demo.spatialdds.org/$SCENE/content/hello",
  "profile": "spatial.manifest/1.8",
  "rtype": "content",
  "content": {
    "content_id": "content/hello",
    "title": "Hello from my world",
    "summary": "A first published object: a glTF marker three metres from the fountain.",
    "class_id": "https://schema.org/CreativeWork",
    "tags": ["hello"]
  },
  "assets": [{
    "uri": "https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf",
    "media_type": "model/gltf+json",
    "hash": "sha256:26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf"
  }],
  "coverage": {
    "frame_ref": {"uuid": "", "fqn": "earth-fixed"},
    "has_bbox": false, "global": false, "has_circle": true,
    "circle_center": [-97.7393, 30.285, 158.0], "circle_radius_m": 3.0
  },
  "stamp": {"sec": "1792800000", "nanosec": 0},
  "ttl_sec": 3600
}
EOF

cat > announce.json <<EOF
{
  "content_id": "content/hello",
  "provider_id": "example.org",
  "title": "Hello from my world",
  "summary": "A first published object: a glTF marker three metres from the fountain.",
  "tags": ["hello"],
  "class_id": "https://schema.org/CreativeWork",
  "manifest_uri": "spatialdds://demo.spatialdds.org/$SCENE/content/hello",
  "coverage": [{
    "has_crs": true, "crs": "EPSG:4326",
    "has_bbox": false, "bbox": [0.0, 0.0, 0.0, 0.0],
    "has_aabb": false, "aabb": {"min_xyz": [0.0, 0.0, 0.0], "max_xyz": [0.0, 0.0, 0.0]},
    "global": false,
    "has_frame_ref": false,
    "frame_ref": {"uuid": "", "fqn": "", "has_coord_convention": false, "coord_convention": "ENU",
                  "has_scale": false, "scale_status": "SCALE_UNKNOWN", "meters_per_unit": 1.0, "display_unit": "m"},
    "has_coverage_window": false,
    "coverage_window_start": {"sec": "0", "nanosec": 0},
    "coverage_window_end": {"sec": "0", "nanosec": 0},
    "has_circle": true, "circle_center": [-97.7393, 30.285, 158.0], "circle_radius_m": 3.0
  }],
  "coverage_frame_ref": {"uuid": "", "fqn": "", "has_coord_convention": false, "coord_convention": "ENU",
                         "has_scale": false, "scale_status": "SCALE_UNKNOWN", "meters_per_unit": 1.0, "display_unit": "m"},
  "has_coverage_eval_time": false,
  "coverage_eval_time": {"sec": "0", "nanosec": 0},
  "transforms": [],
  "available_from": {"sec": "1792800000", "nanosec": 0},
  "available_until": {"sec": "0", "nanosec": 0},
  "stamp": {"sec": "1792800000", "nanosec": 0},
  "ttl_sec": 86400
}
EOF
```

Three rules for these files. **Coordinates** are `[longitude, latitude,
altitude_m]`. **64-bit integers** (`sec`) are decimal strings. **Every `has_*`
flag** sits beside its member, even when false. That is the binding's
canonical JSON, and the registry validates every body against the published
schema. Every `spatialdds://` URI you publish names the registry's authority,
here `demo.spatialdds.org`.

**2. Publish: manifest, then announce.**

<!-- guide: sh -->
```sh
curl -s -X PUT "$BASE/spatialdds/resolve" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @manifest.json -o /dev/null -w "%{http_code}\n"
curl -s -X PUT "$BASE/$SCENE/content/content_announce/v1/content%2Fhello" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @announce.json -o /dev/null -w "%{http_code}\n"
```

```console
201
201
```

The announce's URL is the collection URL plus its key, with the `/` in the key
written as `%2F`.

Your content lane exists now. Start the watcher in a second terminal, with the
same three variables set, and leave it running through the rest of this
section:

<!-- guide: start content-watch -->
```sh
python3 watch.py content/content_announce/v1
```

**3. Read it back,** exactly as any other client will:

<!-- guide: sh -->
```sh
curl -s "$BASE/$SCENE/content/content_announce/v1" \
  | python3 -c 'import json,sys; print(*sorted(c["content_id"] for c in json.load(sys.stdin)), sep="\n")'
curl -s "$BASE/spatialdds/resolve?uri=spatialdds%3A%2F%2Fdemo.spatialdds.org%2F$SCENE%2Fcontent%2Fhello" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["id"]); print(d["assets"][0]["hash"])'
```

```console
content/hello
spatialdds://demo.spatialdds.org/my-world/content/hello
sha256:26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf
```

**4. Update it.** `PUT` the whole record again to the same URL. A new record
answers `201`; a replacement answers `200`.

<!-- guide: sh -->
```sh
sed 's/"title": "Hello from my world"/"title": "Hello again from my world"/' announce.json > announce-v2.json
curl -s -X PUT "$BASE/$SCENE/content/content_announce/v1/content%2Fhello" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @announce-v2.json -o /dev/null -w "%{http_code}\n"
curl -s "$BASE/$SCENE/content/content_announce/v1/content%2Fhello" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["title"])'
```

```console
200
Hello again from my world
```

**5. Withdraw it, both halves.** First the announce, with a reason every
subscriber will see. Then the manifest, after which `resolve` answers `404`.

<!-- guide: sh -->
```sh
curl -s -X DELETE "$BASE/$SCENE/content/content_announce/v1/content%2Fhello?reason=test%20complete" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -o /dev/null -w "%{http_code}\n"
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/$SCENE/content/content_announce/v1/content%2Fhello"
curl -s -X DELETE "$BASE/spatialdds/resolve?uri=spatialdds%3A%2F%2Fdemo.spatialdds.org%2F$SCENE%2Fcontent%2Fhello&reason=test%20complete" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -o /dev/null -w "%{http_code}\n"
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/spatialdds/resolve?uri=spatialdds%3A%2F%2Fdemo.spatialdds.org%2F$SCENE%2Fcontent%2Fhello"
```

```console
204
404
204
404
```

**6. Outside your lane.** The same token writing to another scene is refused,
and the refusal says why:

<!-- guide: sh -->
```sh
curl -s -X PUT "$BASE/ut-austin-south-mall/content/content_announce/v1/content%2Fhello" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @announce.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["title"])'
```

```console
403 Not your lane
```

The watcher replayed the object as it found it, then printed its update and
its withdrawal, with the reason:

<!-- guide: stop content-watch -->
```console
sample content/hello  ''
end_of_replay   ''
sample content/hello  ''
dispose content/hello  'test complete'
```

A subscriber learns of a withdrawal from the stream, with the publisher's
reason, rather than by polling. Stop the watcher with Ctrl-C.

## The same in Python

The client library's builders fill in every member of a record, so a publish
is one call. Reading needs no token:

<!-- guide: python -->
```python
import spatialdds as sd, os
ep = sd.connect(os.environ["BASE"])
svc = next(h for h in ep.search(geohash="9v6krc") if h.serves("geo_anchor"))
for a in svc.topic("geo_anchor").latched():
    print(a["anchor_id"], a["method"], a["geopose"]["lat_deg"], a["geopose"]["lon_deg"])
doc = ep.resolve("spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/fountain-marker")
print(doc.doc["content"]["title"], "|", len(doc.fetch_asset()), "bytes, hash verified")
```

```console
anchor/fountain-north-rim Surveyed 30.28498593 -97.73935
anchor/fountain-south-rim VisualFix 30.28491407 -97.73935
anchor/fountain-east-rim VisualFix 30.28495 -97.73931359
anchor/fountain-west-rim VisualFix 30.28495 -97.73938641
anchor/fountain-plaque GNSS 30.28496797 -97.73932919
Littlefield Fountain marker | 954 bytes, hash verified
```

To publish, connect with your token and your scene. A publish with no scene is
refused, and the refusal names the scenes the endpoint serves.

<!-- guide: python -->
```python
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["SPATIALDDS_TOKEN"],
                scene=os.environ["SCENE"])
ep.publish_content(
    "hello-py", title="Hello from Python", position=(30.2850, -97.7393),
    asset="https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf",
    media_type="model/gltf+json",
    sha256="26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf")
doc = ep.resolve(f"spatialdds://demo.spatialdds.org/{os.environ['SCENE']}/content/hello-py")
print(doc.doc["content"]["title"], "|", len(doc.fetch_asset()), "bytes, hash verified")
ep.withdraw(f"/{os.environ['SCENE']}/content/content_announce/v1", "hello-py")
print("withdrawn")
```

```console
Hello from Python | 954 bytes, hash verified
withdrawn
```

Three things to know about this. The builder keys this record `hello-py`, with
no `content/` prefix. The library's `withdraw` removes the announce and sends
no reason; to withdraw the manifest too, or to give a reason, use the `curl`
`DELETE`s above. And the client sends your token only to the base URL you
connected to, never to the hosts your assets live on.

## Anywhere on Earth

Nothing ties a lane to a place. A record's position is its own coverage, and
any valid latitude and longitude is accepted. The one extra step is for
discovery by location: a geohash search returns the scenes whose **service
announce** covers that place. So publish one service announce for your lane,
covering where your content is, and a search there finds you:

<!-- guide: python -->
```python
import os, urllib.parse, spatialdds as sd
scene = os.environ["SCENE"]
ep = sd.connect(os.environ["BASE"], token=os.environ["SPATIALDDS_TOKEN"], scene=scene)
ep.publish_service(service_id="my-paris", kind="CONTENT",
                   coverage=sd.circle(48.8584, 2.2945, 2000))   # lat, lon, radius in metres
ep.publish_content("eiffel", title="Near the Eiffel Tower", position=(48.8584, 2.2945),
    asset="https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf",
    media_type="model/gltf+json",
    sha256="26c80427c7cbfc9626b57b1a4e25bcf6004332b3758c0ab0e3e73bc1dd53a6cf")
reader = sd.connect(os.environ["BASE"])
for hit in reader.search(geohash="u09tun"):       # a geohash cell over central Paris
    print(hit.service_id, [urllib.parse.urlsplit(t["url"]).path for t in hit.topics])
ep.withdraw(f"/{scene}/content/content_announce/v1", "eiffel")
ep.withdraw(f"/{scene}/discovery/announce/v1", "my-paris")
print("withdrawn:", [h.service_id for h in reader.search(geohash="u09tun")])
```

```console
my-paris ['/my-world/content/content_announce/v1', '/my-world/discovery/announce/v1']
withdrawn: []
```

`sd.circle(lat, lon, radius_m)` sets how wide an area your lane is found in;
publish more than one service announce if your content is in several places.
The announce's `manifest_uri` points at
`spatialdds://demo.spatialdds.org/<scene>/service/<service_id>`, which
resolves `404` until you publish a manifest there; it is optional.

## Populate a world

Content is what a place offers. A world is what is in it. Your lane has a
world lane for entities at `$BASE/$SCENE/world/entity/v1`. It opens with your
first `PUT`, and each entity is keyed by its `entity_id`. An entity has a pose
in a frame you name, and a `basis` that says how you know it:

- **`DECLARED`**: you say so, like a bench. It stays until you withdraw it.
- **`OBSERVED`**: you are seeing it, like a cyclist. It must be refreshed. You
  promise an interval with `?refresh=<seconds>` (1 to 3600) and re-`PUT` the
  entity within it. Stop, and the registry withdraws it with the reason
  `refresh lapsed`; your subscribers hear that as they would any withdrawal.

**1. A bench, declared.** The `frame_ref` names your own local frame: here
`map/my-world`, metres, ENU.

<!-- guide: sh -->
```sh
cat > bench.json <<EOF
{
  "entity_id": "ent:bench", "basis": "DECLARED", "type_uris": ["https://example.org/bench"],
  "layer": "FAST",
  "frame_ref": {"uuid": "", "fqn": "map/$SCENE", "has_coord_convention": true, "coord_convention": "ENU",
                "has_scale": true, "scale_status": "SCALE_DECLARED", "meters_per_unit": 1.0, "display_unit": "m"},
  "has_pose": true, "pose": {"t": [3.0, 4.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]},
  "has_extent": false, "extent": {"min_xyz": [0.0, 0.0, 0.0], "max_xyz": [0.0, 0.0, 0.0]},
  "properties": [{"key": "demo.label", "value": "bench"}], "external_refs": [], "content_refs": [],
  "state": "ACTIVE", "state_reason": "", "source_id": "svc:$SCENE:me",
  "stamp": {"sec": "1793300000", "nanosec": 0}
}
EOF
curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:bench" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @bench.json -o /dev/null -w "%{http_code}\n"
```

```console
201
```

**World lanes need a token to read** as well as to write, by `GET` and by
WebSocket. Any issued token reads any scene's world lanes; a request without
one is refused:

<!-- guide: sh -->
```sh
curl -s "$BASE/$SCENE/world/entity/v1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["title"])'
```

```console
401 Operator-scoped resource
```

**2. A cyclist, observed and refreshed.** The same body with
`"basis": "OBSERVED"` is refused without `?refresh=`. With it, the cyclist is
accepted, and each re-`PUT` inside the interval answers `200`.

<!-- guide: sh -->
```sh
sed -e 's/ent:bench/ent:cyclist/; s/"DECLARED"/"OBSERVED"/; s/bench"/cyclist"/g' bench.json > cyclist.json
curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:cyclist" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @cyclist.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["title"])'
for i in 1 2 3; do
  curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:cyclist?refresh=5" \
    -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
    --data @cyclist.json -o /dev/null -w "%{http_code}\n"
  sleep 2
done
```

```console
400 An observed claim needs a refresh interval
201
200
200
```

**3. Let it lapse.** Stop refreshing, wait past the interval, and the cyclist
is gone, while the bench, which nobody has to refresh, is still there:

<!-- guide: sh -->
```sh
sleep 7
curl -s -o /dev/null -w "cyclist %{http_code}\n" -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/entity/v1/ent:cyclist"
curl -s -o /dev/null -w "bench %{http_code}\n" -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/entity/v1/ent:bench"
```

```console
cyclist 404
bench 200
```

To withdraw an entity yourself, `DELETE` it with a reason:
`$BASE/$SCENE/world/entity/v1/<entity_id>?reason=<url-encoded text>`. Once
opened, a world lane is listed among your service's topics in search, even
after its last entity is withdrawn.

## Claims

A claim is a relationship between entities: "the bench is near the lamp",
"the camera sees the cyclist". Claims live in their own world lane,
`$BASE/$SCENE/world/oarc.relationship/v1`, keyed by the claim's `id`, and are
read with a token like any world lane. The type is the OWM 0.2 relationship
prototype (`oarc_owm.Relationship`), a deployment extension. Every claim you
publish is validated against the schema the registry publishes for the type,
and a body that does not match it is refused with `400`. In 0.8.1 the index
at `$BASE/.well-known/spatialdds/schemas` lists that schema whenever any claim
lane exists on the registry, so on one nobody has written a claim to, it is
not listed yet; listing every writable type always is filed as
[finding 79](https://github.com/OpenArCloud/spatialdds-py/blob/main/WEB-BINDING-FINDINGS.md).
A claim carries the same lifecycle as an entity: a `basis`, a `state`, and a
`state_reason`.

**1. Something to make a claim about.** A second declared entity, a lamp:

<!-- guide: sh -->
```sh
sed -e 's/ent:bench/ent:lamp/; s/bench"/lamp"/g; s/\[3.0, 4.0, 0.0\]/[5.0, 4.0, 0.0]/' bench.json > lamp.json
curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:lamp" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @lamp.json -o /dev/null -w "%{http_code}\n"
```

```console
201
```

**2. Publish a claim.** "The bench is near the lamp", declared. Leave
`asserter` empty:

<!-- guide: sh -->
```sh
cat > near.json <<'EOF'
{
  "id": "near-1", "subject_ref": "ent:bench", "predicate": "https://example.org/predicate/near",
  "object_ref": "ent:lamp", "basis": "DECLARED", "asserter": "",
  "has_confidence": false, "confidence": 0.0, "derived_from": [],
  "stamp": {"sec": "1793300000", "nanosec": 0},
  "has_valid_from": false, "valid_from": {"sec": "0", "nanosec": 0},
  "has_valid_until": false, "valid_until": {"sec": "0", "nanosec": 0},
  "meta": [], "state": "CREATED", "state_reason": ""
}
EOF
curl -s -X PUT "$BASE/$SCENE/world/oarc.relationship/v1/near-1" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @near.json -o /dev/null -w "%{http_code}\n"
```

```console
201
```

Your claim lane exists now. Watch it in your second terminal while you work
through the rest of this section:

<!-- guide: start claim-watch -->
```sh
python3 watch.py world/oarc.relationship/v1
```

**3. Read it back.** One claim by its id, or the whole lane:

<!-- guide: sh -->
```sh
curl -s -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/oarc.relationship/v1/near-1" \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["subject_ref"], c["predicate"].rsplit("/",1)[-1], c["object_ref"], "|", c["basis"], c["state"], "| asserter:", c["asserter"])'
curl -s -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/oarc.relationship/v1" \
  | python3 -c 'import json,sys; print([c["id"] for c in json.load(sys.stdin)])'
```

```console
ent:bench near ent:lamp | DECLARED CREATED | asserter: my-group
['near-1']
```

**The asserter is your token's principal.** A claim is only as good as who
makes it, so over HTTP the registry grounds the asserter in the credential: an
empty `asserter` is stamped with your principal's name, as above, and any other
name is refused. You may also write your own name there; it is then checked
rather than stamped.

<!-- guide: sh -->
```sh
sed 's/"asserter": ""/"asserter": "somebody-else"/' near.json > near-forged.json
curl -s -X PUT "$BASE/$SCENE/world/oarc.relationship/v1/near-1" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @near-forged.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["title"])'
```

```console
403 You may only assert as yourself
```

**4. The basis decides whether a claim must be refreshed.** The rule is the
entity rule. A `DECLARED` claim, like the one above, holds until withdrawn and
takes no `?refresh=`. An `OBSERVED` or `DERIVED` claim says somebody is
watching, so it must promise an interval and keep it, or the registry
withdraws it with the reason `refresh lapsed`. Here a camera of yours sees the
bench, for one refresh and no more:

<!-- guide: sh -->
```sh
sed -e 's/"near-1"/"sees-1"/; s/"DECLARED"/"OBSERVED"/; s/ent:bench/ent:camera/; s/predicate\/near/predicate\/sees/; s/ent:lamp/ent:bench/' near.json > sees.json
curl -s -X PUT "$BASE/$SCENE/world/oarc.relationship/v1/sees-1" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @sees.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["title"])'
curl -s -X PUT "$BASE/$SCENE/world/oarc.relationship/v1/sees-1?refresh=5" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" \
  --data @sees.json -o /dev/null -w "%{http_code}\n"
sleep 7
curl -s -o /dev/null -w "sees-1 after its interval: %{http_code}\n" -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/oarc.relationship/v1/sees-1"
curl -s -o /dev/null -w "near-1 under the same silence: %{http_code}\n" -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/oarc.relationship/v1/near-1"
```

```console
400 An observed claim needs a refresh interval
201
sees-1 after its interval: 404
near-1 under the same silence: 200
```

**5. Withdraw a claim, with a reason.** `DELETE` it; the reason travels to
every subscriber with the claim's last state:

<!-- guide: sh -->
```sh
curl -s -X DELETE "$BASE/$SCENE/world/oarc.relationship/v1/near-1?reason=lamp%20moved" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -o /dev/null -w "%{http_code}\n"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $SPATIALDDS_TOKEN" "$BASE/$SCENE/world/oarc.relationship/v1/near-1"
```

```console
204
404
```

The watcher on the claim lane printed each claim's life, the lapse and the
withdrawal each arriving as a `RETIRED` sample with its reason, then the
dispose:

<!-- guide: stop claim-watch -->
```console
sample near-1 CREATED ''
end_of_replay   ''
sample sees-1 CREATED ''
sample sees-1 RETIRED 'refresh lapsed'
dispose sees-1  'refresh lapsed'
sample near-1 RETIRED 'lamp moved'
dispose near-1  'lamp moved'
```

Clean up the two entities the same way:

<!-- guide: sh -->
```sh
for e in ent:bench ent:lamp; do
  curl -s -X DELETE "$BASE/$SCENE/world/entity/v1/$e?reason=guide%20complete" \
    -H "Authorization: Bearer $SPATIALDDS_TOKEN" -o /dev/null -w "$e %{http_code}\n"
done
```

```console
ent:bench 204
ent:lamp 204
```

## What to expect

A `PUT` answers `201` when it creates a record and `200` when it replaces
one; a `DELETE` answers `204`. Every refusal is a JSON problem document (RFC
7807, Appendix N.8) whose `type` ends in the slug below, with a `title`, and a
`detail` that says what to fix. Here are three, as a client sees them:

<!-- guide: sh -->
```sh
p() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["type"].rsplit(":",1)[-1], "|", d["title"])'; }
curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:bench" -H "Content-Type: application/json" --data @bench.json | p
curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:bench" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" --data '{"entity_id": "ent:bench"}' | p
curl -s -X PUT "$BASE/$SCENE/world/entity/v1/ent:other" \
  -H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json" --data @bench.json | p
```

```console
401 operator-scoped | Operator-scoped resource
400 schema-invalid | Body does not match its schema
400 key-mismatch | Body does not match the URL
```

Every row of this table is reproduced by the gate, and its status, type and
title compared with what is printed here.

<!-- guide: errors -->
| Status | Type | Title | When | What to do |
| --- | --- | --- | --- | --- |
| 400 | `schema-invalid` | Body does not match its schema | The body does not match its schema; `detail` names the member | Send the whole record: every member, every `has_*` flag. A body is never a patch |
| 400 | `not-json` | Body is not JSON | The body does not parse as JSON | Send canonical JSON |
| 400 | `bad-key` | Unusable instance key | A key in the URL is empty or holds a control character | Use a plain name; write `/` in a key as `%2F` |
| 400 | `key-mismatch` | Body does not match the URL | The key in the URL is not the body's own key member | `PUT` each record to the URL of its own key |
| 400 | `refresh-required` | An observed claim needs a refresh interval | An `OBSERVED` or `DERIVED` entity or claim sent without `?refresh=` | Promise an interval, and re-`PUT` within it |
| 400 | `refresh-not-applicable` | A declaration has no refresh | A `DECLARED` or `AUTHORED` entity or claim sent with `?refresh=` | Drop `?refresh=`; a declaration holds until withdrawn |
| 400 | `refresh-invalid` | Refresh interval out of range | `?refresh=` is not a number of seconds from 1 to 3600 | Choose an interval in range |
| 401 | `operator-scoped` | Operator-scoped resource | No token, or a token nobody issued, on a write; or any read of a world lane without one | Check the `Authorization: Bearer` header |
| 403 | `not-your-lane` | Not your lane | A write outside the scenes your token may write | Write to your own lane |
| 403 | `quota-exceeded` | Record quota reached | A new record when your lane is at its quota | Withdraw something, or ask for more. Replacing a record is always allowed |
| 403 | `asserter-not-principal` | You may only assert as yourself | A claim whose `asserter` is not your principal | Leave `asserter` empty, and it is stamped |
| 404 | `no-such-instance` | No such instance | A key that is not there, including an entity or claim whose refresh lapsed | Check the key and its `%2F`; refresh within the interval |
| 404 | `no-such-manifest` | No manifest for that URI | A `spatialdds://` URI this registry holds no manifest for | Check the URI; publish the manifest first |
| 405 | `no-route` | Method Not Allowed | A verb this path does not take, such as `PUT` on a collection URL | Writes go to instance URLs; a collection URL takes `GET` |
| 405 | `not-publishable` | That topic is not publishable | A write to a lane of a type this registry does not accept writes for; `detail` lists those it does | Write to one of the types listed |
| 413 | `body-too-large` | Request body too large | A body over 256 KiB | Keep records small; host large assets elsewhere and reference them by URL and hash |
| 414 | `uri-too-long` | Request target too long | A path and query over 16 KiB | Shorten the URL |
| 415 | `unsupported-media-type` | Body is not JSON by its content type | A write body not sent as `application/json` | Add `-H "Content-Type: application/json"` |
| 429 | `rate-limited` | Too many requests | More than 20 requests a second from one address (bursts up to 60) | Wait for the `Retry-After` header, then retry |
| 503 | `too-many-subscriptions` | Too many subscriptions | 256 WebSocket streams are already open | Retry later; plain `GET`s are unaffected |
| 503 | `journal-unwritable` | The registry cannot record writes | The registry's disk is full or cannot be written. The write was not accepted, and reads are unaffected | Wait for the `Retry-After` header, then send the same write again |

Two behaviours worth knowing:

- **Identity comes from your token, not your body.** The registry records
  which token wrote each entry, and grounds a claim's asserter in it. A
  record's `provider_id` is your own statement, carried as written.
- **A known issue in 0.8.1:** a refused write to a lane that does not exist
  yet still opens it. That covers a `PUT` refused with `400` and a `DELETE`
  answered with `404`. The lane is left empty and is listed in search until
  the operator resets it. It is filed for a fix; until then, check a new
  lane's first body before you send it.

## The demo.spatialdds.org sandbox

Everything above is the protocol and the `spatialdds` package. This section
is the public sandbox's own contract: its operator's settings and promises,
which another registry may set differently.

| | |
| --- | --- |
| Base URL | `https://demo.spatialdds.org`, HTTPS and WSS only; plain HTTP redirects |
| Authority | `demo.spatialdds.org`: every `spatialdds://` URI you publish there names it |
| Served by | `spatialdds` 0.8.1, SpatialDDS 1.8 web binding, with the `oarc.relationship` claim lane |
| Sample scene | `ut-austin-south-mall`: 1 service, 5 surveyed anchors, 2 content records at UT Austin. Readable by everyone, writable by its operator only |
| Your lane | One scene, named when your token is issued. Anywhere on Earth: the server's name is an address, not a location |
| Quota | 500 records in your lane (instances plus manifests); replacing a record never counts against it |
| Limits | 20 requests a second per client address, bursts up to 60; bodies up to 256 KiB; URLs up to 16 KiB; 256 open WebSocket streams |
| Reads | Open, with no token and no account, for every scene, except world lanes (entities and claims), which any issued token reads |
| API reference | `https://demo.spatialdds.org/.well-known/spatialdds/openapi.html`, generated from the running service |

- **Writes persist.** Every accepted write is journalled to disk before it is
  acknowledged, and survives a restart, a crash and a reboot of the host. The
  exceptions are `OBSERVED` and `DERIVED` entities and claims, which last only
  as long as you keep refreshing them.
- **Your lane is yours.** No other token can write to it, and yours cannot
  write anywhere else.
- **Your lane can be reset on its own.** The operator can return your lane to
  empty without touching any other. A reset is deliberate and announced to you
  first, never routine.
- **Your token is yours alone.** It can be rotated or revoked without
  affecting anyone else. If it leaks, ask for a new one; the old one stops
  working at once.
- **It is a sandbox.** There is no service-level guarantee. Everything you
  write is readable by anyone, and world lanes by anyone holding a token, so
  publish nothing private.

Questions, a token, a larger quota, or a reset:
[contact Open AR Cloud](https://openarcloud.org/about/#contact).
