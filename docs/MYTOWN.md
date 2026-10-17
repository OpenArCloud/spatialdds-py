# Standing up a registry: the mytown walkthrough

Eight steps, from nothing to a registry somebody else can read, with no clone
and no recording to find first. Every command here is run by
`gates/quickstart.py` exactly as it is printed, against the endpoint the serve
line above it starts, and the outputs below are what it printed.

The companion document is [WALKTHROUGH.md](WALKTHROUGH.md), which does the
same journey with `curl` and no library at all. This one is the short way; that
one is the proof that the short way hides nothing.

## 1. Install it

```sh
pip install "spatialdds[server]"             # once 0.2.0 is on PyPI
pip install "./lib/spatialdds[server]"       # from this checkout, today
```

The `server` extra is what makes you an endpoint: `aiohttp` to serve, `mcap`
for the journal, `jsonschema` to validate every published body before it
reaches the wire. Reading somebody else's registry needs none of them --
`pip install spatialdds` is two dependencies.

## 2. Serve it empty

```sh
rm -f /tmp/mytown-registry.mcap
python -m spatialdds.serve --authority registry.mytown.example \
    --publish --token tok --journal /tmp/mytown-registry.mcap --port 8787
```

`--token tok` is here so that every block below can be pasted as it stands.
Leave it out and the registry mints one and prints it, which is what you want
anywhere real.

That is a working registry. It holds nothing, and it already knows what a
`GeoAnchor` is: the types come from the IDL pinned inside the package, at the
specification commit the banner prints. There is no base recording to download
and nothing to seed.

The banner also prints the bearer token and the export lines for a second
terminal. The rest of this page runs there, and every block sets what it
needs, so you can paste any one of them into a fresh shell.

## 3. Announce a service, with its contract

A service announcement says a VPS is here and roughly where. The manifest
beside it is the contract: which profiles it implements, which topics it reads
and writes, what it will refuse beyond, how to present a credential. Both in
one call, and every claim in them is something you said.

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import json, os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"], scene="mytown")
WHEN = "2026-10-16T15:30:00Z"   # pinned, so this page prints the same twice
area = sd.circle(30.2849, -97.7393, 500)
w = ep.publish_service(
    when=WHEN,
    service_id="mytown-vps", kind="VPS",
    name="Mytown visual positioning", org="mytown.example",
    version="2026-q4", coverage=area,
    caps=sd.capabilities(
        supported=[sd.profile("spatial.core", minor=8),
                   sd.profile("spatial.disco", minor=8)],
        features=["web-binding/json"]),
    topics=[sd.topic("geo_anchor", scene="mytown", stream="frames")],
    manifest=sd.service_manifest(
        "mytown-vps", "VPS", authority=ep.authority, scene="mytown",
        name="Mytown visual positioning", org="mytown.example",
        version="2026-q4", profiles=["Core", "AR+Geo"],
        request={"features_topic": "feat.keyframe",
                 "image_blob_role": "image/jpeg"},
        response={"rich": "pg.nodegeo", "minimal": "geo.fix"},
        limits={"max_fps": 10, "max_image_px": 1920},
        auth={"scheme": "bearer", "issuer": "https://registry.mytown.example"},
        coverage=area, cells=sd.cells(area), when=WHEN))
print("announce created:", w.created, "| manifest created:", w.manifest.created)
print(json.dumps(w.manifest.doc, indent=1, sort_keys=True))
PY
```

```console
announce created: True | manifest created: True
{
 "auth": {
  "issuer": "https://registry.mytown.example",
  "scheme": "bearer"
 },
 "coverage": {
  "circle_center": [
   -97.7393,
   30.2849,
   0.0
  ],
  "circle_radius_m": 500.0,
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
 "id": "spatialdds://registry.mytown.example/mytown/service/mytown-vps",
 "limits": {
  "max_fps": 10,
  "max_image_px": 1920
 },
 "profile": "spatial.manifest/1.8",
 "profiles": [
  "Core",
  "AR+Geo"
 ],
 "request": {
  "features_topic": "feat.keyframe",
  "image_blob_role": "image/jpeg"
 },
 "response": {
  "minimal": "geo.fix",
  "rich": "pg.nodegeo"
 },
 "rtype": "service",
 "service": {
  "kind": "VPS",
  "name": "Mytown visual positioning",
  "org": "mytown.example",
  "service_id": "mytown-vps",
  "version": "2026-q4"
 },
 "stamp": {
  "nanosec": 0,
  "sec": "1792164600"
 },
 "ttl_sec": 1800
}
```

That is the whole document the resolver will serve, printed because a publisher
should see what they published rather than trust that it happened. `when=` is
pinned only so this page prints the same thing twice; leave it out and the
record is stamped at the moment you published it, which is what you want.

**Nothing in it was filled in for you.** The builders put a stamp on it, a
frame under the coverage and a checksum on an anchor, because those are
plumbing with one sensible value. Profiles, limits, request and response
topics, an auth scheme: those are claims, and a library that defaulted any of
them would be making promises on your behalf. Say nothing and you publish a
minimal record, which is honest and is what the front page's short version
does.

## 4. Publish a piece of content

Two PUTs, manifest first, then the announce that points at it. The asset is
hashed here from the bytes on disk, so the hash in the manifest is one a reader
can check rather than one somebody typed.

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import json, os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"], scene="mytown")
w = ep.publish_content(
    when="2026-10-16T15:30:00Z",   # pinned, as above
    content_id="content/duck", title="A duck at the fountain",
    summary="An original glTF duck, placed by its own pose.",
    tags=["duck", "glTF", "walkthrough"],
    class_id="https://schema.org/CreativeWork",
    position=(30.2849, -97.7393), radius_m=2,
    asset=sd.asset("samples/assets/duck.gltf",
                   media_type="model/gltf+json",
                   uri="https://registry.mytown.example/assets/duck.gltf"))
print("content created:", w.created, "| manifest created:", w.manifest.created)
print(json.dumps(w.manifest.doc, indent=1, sort_keys=True))
PY
```

```console
content created: True | manifest created: True
{
 "assets": [
  {
   "hash": "sha256:8f73ee013981fc5c6ff4f0bb564b18a2a227fd25ba872f1f47bf9b87a3e488fe",
   "media_type": "model/gltf+json",
   "meta": [
    {
     "entries": [
      {
       "key": "bytes",
       "value": "1432"
      }
     ],
     "json": "",
     "namespace": "spatialdds-web"
    }
   ],
   "uri": "https://registry.mytown.example/assets/duck.gltf"
  }
 ],
 "content": {
  "class_id": "https://schema.org/CreativeWork",
  "content_id": "content/duck",
  "summary": "An original glTF duck, placed by its own pose.",
  "tags": [
   "duck",
   "glTF",
   "walkthrough"
  ],
  "title": "A duck at the fountain"
 },
 "coverage": {
  "circle_center": [
   -97.7393,
   30.2849,
   0.0
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
 "id": "spatialdds://registry.mytown.example/mytown/content/duck",
 "profile": "spatial.manifest/1.8",
 "rtype": "content",
 "stamp": {
  "nanosec": 0,
  "sec": "1792164600"
 },
 "ttl_sec": 3600
}
```

`sd.asset()` read the file, hashed it and counted the bytes. The other form,
`sd.asset_ref(url, hash=...)`, is for an asset somebody else hosts: you supply
the hash because you are the one who has the bytes. Give it no hash and the
reference carries no integrity claim, which costs a reader exactly one thing --
`fetch_asset()` refuses instead of reporting a check it could not do.

## 5. Pin it with an anchor

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import json, os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"], scene="mytown")
w = ep.publish_anchor("anchor/duck-perch", when="2026-10-16T15:30:00Z",
                      lat=30.2849, lon=-97.7393,
                      alt_m=158.0, method="VisualFix",
                      sigma_enu_m=(0.25, 0.25, 0.5), content="duck")
a = w.doc
print("anchor", a["anchor_id"], "method", a["method"],
      "confidence", a["confidence"])
print("names   ", a["manifest_uri"])
print(json.dumps(a, indent=1, sort_keys=True))
PY
```

```console
anchor anchor/duck-perch method VisualFix confidence 0.6667
names    spatialdds://registry.mytown.example/mytown/content/duck
{
 "anchor_id": "anchor/duck-perch",
 "checksum": "sha256:24755608268c4650718f27d1105d1c10bb130f23b4dafe19e6d35ab69a2c732e",
 "confidence": 0.6667,
 "cov": {
  "pos": [
   0.0625,
   0.0,
   0.0,
   0.0,
   0.0625,
   0.0,
   0.0,
   0.0,
   0.25
  ],
  "type": "COV_POS3"
 },
 "frame_ref": {
  "coord_convention": "ENU",
  "display_unit": "",
  "fqn": "earth-fixed",
  "has_coord_convention": false,
  "has_scale": false,
  "meters_per_unit": 0.0,
  "scale_status": "SCALE_UNKNOWN",
  "uuid": ""
 },
 "geopose": {
  "alt_m": 158.0,
  "cov": {
   "pos": [
    0.0625,
    0.0,
    0.0,
    0.0,
    0.0625,
    0.0,
    0.0,
    0.0,
    0.25
   ],
   "type": "COV_POS3"
  },
  "lat_deg": 30.2849,
  "lon_deg": -97.7393,
  "q": [
   0.0,
   0.0,
   0.0,
   1.0
  ],
  "stamp": {
   "nanosec": 0,
   "sec": "1792164600"
  }
 },
 "manifest_uri": "spatialdds://registry.mytown.example/mytown/content/duck",
 "map_id": "",
 "method": "VisualFix"
}
```

**Method is the claim level.** `VisualFix` with ±0.5 m up is what this record
asserts, and publishing it does not make it surveyed truth. `confidence` is
`1/(1+sigma_max)`, computed from the same covariance, so the scalar and the
matrix cannot contradict each other.

## 6. Find it the way a stranger would

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
    | python3 -c 'import json,sys; s=json.load(sys.stdin)["results"][0]["service"]; print(s["service_id"], s["kind"]); [print(" ", t["type"], t["url"]) for t in s["topics"]]'
```

```console
mytown-vps VPS
  content_announce https://registry.mytown.example/mytown/content/content_announce/v1
  announce https://registry.mytown.example/mytown/discovery/announce/v1
  geo_anchor https://registry.mytown.example/mytown/frames/geo_anchor/v1
```

No token: discovery and the records themselves are public-cacheable. That is
one `curl` against a registry that did not exist ten minutes ago.

## 7. Resolve the URI to the manifest

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s "$BASE/.well-known/spatialdds/resolver" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("authority", d["authority"]); print("resolve  ", d["https_base"])'
curl -s "$BASE/spatialdds/resolve?uri=spatialdds%3A%2F%2Fregistry.mytown.example%2Fmytown%2Fcontent%2Fduck" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("manifest ", d["id"]); print("asset    ", d["assets"][0]["uri"])'
```

```console
authority registry.mytown.example
resolve   https://registry.mytown.example/spatialdds/resolve
manifest  spatialdds://registry.mytown.example/mytown/content/duck
asset     https://registry.mytown.example/assets/duck.gltf
```

§7.5.2, end to end: the endpoint says which authority it answers for and where
to resolve, and the URI the anchor named comes back as a manifest with the
asset in it.

## 8. Restart it, and see that it is a registry

Stop the process -- Ctrl-C -- and start it again on the same journal:

```sh
python -m spatialdds.serve --authority registry.mytown.example \
    --publish --token tok --journal /tmp/mytown-registry.mcap --port 8787
```

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"])
hit = ep.search(geohash="9v6krc")[0]
print("after restart:", hit.service_id, hit.kind)
for t in hit.topics:
    print(" ", t["type"], len(hit.topic(t["type"]).latched()), "instance(s)")
print("resolves ", ep.resolve(
    "spatialdds://registry.mytown.example/mytown/content/duck").doc["id"])
PY
```

```console
after restart: mytown-vps VPS
  content_announce 1 instance(s)
  announce 1 instance(s)
  geo_anchor 1 instance(s)
resolves  spatialdds://registry.mytown.example/mytown/content/duck
```

Everything is still served: both records, both manifests, the anchor. The
journal the registry replayed is an ordinary SpatialDDS recording -- the same
reader that reads `samples/fountain-anchors.mcap` reads it, and a backup is
therefore a recording rather than a private format.

## What this walkthrough does not cover

Deferred, and stated rather than implied:

* **Leases.** Nothing here expires. A record stays until it is withdrawn, and
  `ttl_sec` is advice to caches rather than a lifetime the registry enforces.
* **Per-provider scopes.** One token, one operator. Publishing as several
  providers with separate credentials is identity design, not a flag.
* **Server-set identity.** `provider_id` and `org` are whatever the publisher
  says. A registry that vouched for them would be making a claim nobody
  authorised it to make.
* **Upstream subscriptions.** This registry does not follow anybody else's.
  Federation between registries is a 1.9 conversation.

All four are on the 1.9 candidates list, and none of them is a gap in the
binding: they are decisions nobody has made yet.
