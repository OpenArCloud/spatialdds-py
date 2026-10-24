# spatialdds-web

A registry for spatial services, content and anchors, over plain HTTPS and
WebSocket. Run one, publish to it, and read from it in a few lines.

## Run a registry

```sh
pip install "spatialdds[server]"
pip install "./lib/spatialdds[server]"       # from this checkout, today
```

```sh
python -m spatialdds.serve --demo --authority demo.spatialdds.org --token tok
```

The banner prints a token and the exports for your second terminal. `--demo`
seeds it with a small sample scene; drop the flag for an empty registry of your
own. `--token tok` is here so that every block below can be pasted as it
stands. Leave it out and the registry mints one and prints it, which is what
you want anywhere real.

Every block below sets what it needs, so you can paste any one of them into a
fresh shell without having run the others.

## Read

<!-- bar: read -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787")
svc = next(h for h in ep.search(geohash="9v6krc") if h.serves("geo_anchor"))
for a in svc.topic("geo_anchor").latched():
    print(a["anchor_id"], a["method"], a["geopose"]["lat_deg"], a["geopose"]["lon_deg"])
```

```console
anchor/fountain-north-rim Surveyed 30.28498593 -97.73935
anchor/fountain-south-rim VisualFix 30.28491407 -97.73935
anchor/fountain-east-rim VisualFix 30.28495 -97.73931359
anchor/fountain-west-rim VisualFix 30.28495 -97.73938641
anchor/fountain-plaque GNSS 30.28496797 -97.73932919
```

Picked by what it serves, not by position: a search result is ordered by the
endpoint, and publishing another service into the same cell changes which one
comes first.

Those are native values, not canonical JSON. Appendix N.1 puts every 64-bit
integer on the wire as a decimal string, and undoing that is most of what the
client library is for.

## Publish

<!-- bar: publish -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787", token="tok", scene="mytown")
ep.publish_service(service_id="mytown-vps", kind="VPS",
                   coverage=sd.circle(30.2849, -97.7393, 500))
ep.publish_content(content_id="duck", title="A duck at the fountain",
                   position=(30.2849, -97.7393),
                   asset="https://cdn.example/duck.gltf")
for hit in ep.search(geohash="9v6krc"):
    print("service", hit.service_id, hit.kind)
```

```console
service mytown-vps VPS
service fountain-anchors CONTENT
```

One call each. An `Announce` has eighteen members and a `ContentAnnounce`
sixteen; what the builders put in the ones you did not mention is listed field
by field by `python3 gates/builders.py --census`, because a record that
validates and says something you did not choose is worse than no builder at
all.

**These two calls are short because they say little, not because records are
meant to be thin.** The same two verbs publish a full service contract and a
hashed asset in the same one call each:
[docs/MYTOWN.md](docs/MYTOWN.md) does exactly that, and
`gates/builders.py` rebuilds this repository's hand-authored records through
the builders to prove nothing is out of reach.

## Resolve

<!-- bar: resolve -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787")
doc = ep.resolve("spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/fountain-marker")
print(doc.doc["content"]["title"])
print(doc.asset_url())
```

```console
Littlefield Fountain marker
https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/samples/assets/fountain-marker.gltf
```

`doc.fetch_asset()` returns those bytes, verified against the hash the manifest
declared, and raises rather than reporting a check it did not perform. This
manifest points at a public CDN, so fetching it needs the network;
[the library's own page](lib/spatialdds/README.md) has the two-line version
that runs offline.

## No SDK required

The package is convenience, not gatekeeping. The whole protocol is plain web:

<!-- gate: authority sh-ok -->
```sh
curl -s "http://127.0.0.1:8787/.well-known/spatialdds/search?geohash=9v6krc"
```

[examples/](examples/) has the raw curl, browser-console and ten-line Python
versions of everything above.

## Going further

| | |
| --- | --- |
| [Your own registry in eight steps](docs/MYTOWN.md) | from nothing to a registry a stranger can read, persistence included |
| [Lanes: one registry, several publishers](docs/PUBLISH.md#access) | tokens scoped to scenes, a `403` across lanes, per-lane reset; virtual authorities designed, not built |
| [Publish and retrieve, step by step](docs/WALKTHROUGH.md) | every record shown in full on the wire |
| [The client library](lib/spatialdds/README.md) | the full API, the lift, errors, integrity |
| [What's on the wire](docs/WIRE.md) | paths, formats, the binding itself |
| [Why believe any of this](docs/EVIDENCE.md) | every claim on this page is executed by a gate on every run |

Implements SpatialDDS 1.8 (Appendix N) as of spec `9c3e1f2`. Conformance: 14
checks, 14 passed, against this repository's own gateway in every shape that
serves data. A registry that holds nothing yet is a shape the suite cannot
certify, for a reason that is the suite's and not this endpoint's; see
[EVIDENCE.md](docs/EVIDENCE.md).
