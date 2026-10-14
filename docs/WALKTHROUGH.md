# A VPS and a duck, published and followed back

Two publishers, end to end, over the implementation-defined write surface
([PUBLISH.md](PUBLISH.md)): a localization service with a resolvable manifest,
and a piece of 3D content followed from `PUT` to fetched model bytes. Both land
*beside* the fountain recording's own records, not in place of them — the point
is published records showing up as siblings of recorded ones.

Every command below runs verbatim against the endpoint the serve line starts,
and `gates/quickstart.py` runs them in this order on every gate run.

**One honesty note, which is the exhibit rather than a footnote.** The duck is
placed by its own announce's position and coverage. No anchor references it,
because no SpatialDDS 1.8 anchor type can carry a content reference — that is
1.9 candidates entry 2, and this walkthrough is what the entry is about. The
linkage is not faked.

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

```sh
export BASE=http://127.0.0.1:8787
export TOK=tok
```

## 1. The VPS announce

<!-- gate: publish sh -->
```sh
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service"]["record"]))' \
  | curl -s -X PUT "$BASE/ut-austin-south-mall/discovery/announce/v1/austin-vps" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["service_id"], "|", d["kind"], "|", d["manifest_uri"])'
```

```console
austin-vps | VPS | spatialdds://demo.spatialdds.org/south-mall/service/austin-vps
```

## 2. The VPS manifest

The announce names a `manifest_uri`; this is the manifest it names, published
into the same store the resolver reads.

<!-- gate: publish sh -->
```sh
python3 -c 'import json; print(json.dumps(json.load(open("samples/published-records.input.json"))["service_manifest"]))' \
  | curl -s -X PUT "$BASE/spatialdds/resolve" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["rtype"], d["id"], "ttl", d["ttl_sec"])'
```

```console
service spatialdds://demo.spatialdds.org/south-mall/service/austin-vps ttl 1800
```

## 3. The duck's manifest

<!-- gate: publish sh -->
```sh
python3 -c 'import json; print(json.dumps(json.load(open("samples/duck.input.json"))["manifest"]))' \
  | curl -s -X PUT "$BASE/spatialdds/resolve" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); a=d["assets"][0]; print(d["id"], "->", a["media_type"], a["uri"])'
```

```console
spatialdds://demo.spatialdds.org/south-mall/content/duck -> model/gltf+json https://demo.spatialdds.org/assets/duck.gltf
```

## 4. The duck's announce

<!-- gate: publish sh -->
```sh
python3 -c 'import json; print(json.dumps(json.load(open("samples/duck.input.json"))["announce"]["record"]))' \
  | curl -s -X PUT "$BASE/ut-austin-south-mall/content/content_announce/v1/content%2Fduck" \
      -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d["coverage"][0]; print(d["content_id"], "at", c["circle_center"][1], c["circle_center"][0], "r", c["circle_radius_m"])'
```

```console
content/duck at 30.2849859 -97.73935 r 2.0
```

## 5. Search finds the service

<!-- gate: publish sh -->
```sh
curl -s "$BASE/.well-known/spatialdds/search?geohash=9v6krc" \
  | python3 -c 'import json,sys; print(", ".join(sorted(r["service"]["service_id"] for r in json.load(sys.stdin)["results"])))'
```

```console
austin-vps, fountain-anchors
```

## 6. The duck is a sibling, not a replacement

<!-- gate: publish sh -->
```sh
curl -s "$BASE/ut-austin-south-mall/content/content_announce/v1" \
  | python3 -c 'import json,sys; print(", ".join(sorted(c["content_id"] for c in json.load(sys.stdin))))'
```

```console
content/duck, content/fountain-label, content/fountain-marker
```

## 7. Resolve the URI, fetch the bytes

<!-- gate: publish sh -->
```sh
curl -s --get "$BASE/spatialdds/resolve" \
      --data-urlencode "uri=spatialdds://demo.spatialdds.org/south-mall/content/duck" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["assets"][0]["hash"])'
curl -s "$BASE/assets/duck.gltf" | wc -c | tr -d " "
```

```console
sha256:8f73ee013981fc5c6ff4f0bb564b18a2a227fd25ba872f1f47bf9b87a3e488fe
1432
```

That last pair is the whole claim: a URI that did not exist before step 3
resolves to a manifest whose ref returns the committed asset's exact bytes.
`gates/publish_duck.py` byte-compares them against `samples/assets/duck.gltf`.
