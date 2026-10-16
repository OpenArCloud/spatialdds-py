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

## 3. Announce a service

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"])
w = ep.publish_service(service_id="mytown-vps", kind="VPS",
                       name="Mytown visual positioning",
                       org="mytown.example",
                       coverage=sd.circle(30.2849, -97.7393, 500))
print("announce created:", w.created)
print("searchable in cell", sd.cells(sd.circle(30.2849, -97.7393, 500))[0])
PY
```

```console
announce created: True
searchable in cell 9v6krc
```

`w.etag` is there too -- the digest of the record's canonical form, which a
conditional write uses -- and this page does not print it, because the record
carries `stamp: now` and so its digest is different every time anybody runs
this. A documented output has to be a function of committed inputs or it is
not something a page can promise.

One call, eighteen members. What went into the fifteen you did not mention is
printed by `python3 gates/builders.py --census`: `caps` zeroed because profile
negotiation is not this binding's, `transforms` empty because no frame
transform was asserted, `stamp` set to now, `coverage_source_ids` empty
because provenance is deferred.

## 4. Publish a piece of content

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"])
w = ep.publish_content(content_id="duck", title="A duck at the fountain",
                       summary="An original glTF duck.",
                       tags=["duck", "glTF"],
                       position=(30.2849, -97.7393), radius_m=2,
                       asset="https://cdn.example/duck.gltf",
                       media_type="model/gltf+json")
print("content created:", w.created, "| manifest created:", w.manifest.created)
print("manifest id:", w.manifest.doc["id"])
PY
```

```console
content created: True | manifest created: True
manifest id: spatialdds://registry.mytown.example/default/content/duck
```

Two PUTs: the manifest first, then the announce that points at it, so the URI
the announce names resolves the moment the announce is visible. The asset was
published with no digest, which is honest rather than convenient -- a reader
who fetches it gets `UnverifiableIntegrity` instead of a check that silently
did nothing. Pass `sha256=` when you know it.

## 5. Pin it with an anchor

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787 TOK=tok
python3 - <<'PY'
import os, spatialdds as sd
ep = sd.connect(os.environ["BASE"], token=os.environ["TOK"])
w = ep.publish_anchor("anchor/duck-perch", lat=30.2849, lon=-97.7393,
                      alt_m=158.0, method="VisualFix",
                      sigma_enu_m=(0.25, 0.25, 0.5), content="duck")
a = w.doc
print("anchor", a["anchor_id"], "method", a["method"],
      "confidence", a["confidence"])
print("names   ", a["manifest_uri"])
PY
```

```console
anchor anchor/duck-perch method VisualFix confidence 0.6667
names    spatialdds://registry.mytown.example/default/content/duck
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
  content_announce https://registry.mytown.example/default/content/content_announce/v1
  announce https://registry.mytown.example/default/discovery/announce/v1
  geo_anchor https://registry.mytown.example/default/frames/geo_anchor/v1
```

No token: discovery and the records themselves are public-cacheable. That is
one `curl` against a registry that did not exist ten minutes ago.

## 7. Resolve the URI to the manifest

<!-- gate: registry sh -->
```sh
export BASE=http://127.0.0.1:8787
curl -s "$BASE/.well-known/spatialdds/resolver" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("authority", d["authority"]); print("resolve  ", d["https_base"])'
curl -s "$BASE/spatialdds/resolve?uri=spatialdds%3A%2F%2Fregistry.mytown.example%2Fdefault%2Fcontent%2Fduck" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("manifest ", d["id"]); print("asset    ", d["assets"][0]["uri"])'
```

```console
authority registry.mytown.example
resolve   https://registry.mytown.example/spatialdds/resolve
manifest  spatialdds://registry.mytown.example/default/content/duck
asset     https://cdn.example/duck.gltf
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
    "spatialdds://registry.mytown.example/default/content/duck").doc["id"])
PY
```

```console
after restart: mytown-vps VPS
  content_announce 1 instance(s)
  announce 1 instance(s)
  geo_anchor 1 instance(s)
resolves  spatialdds://registry.mytown.example/default/content/duck
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
