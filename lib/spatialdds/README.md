# spatialdds

SpatialDDS over plain HTTPS and a WebSocket, for programs.

```sh
pip install spatialdds
```

`fetch` and a WebSocket really are the whole client — the endpoint's own README
shows that in three lines of `curl`, and nothing here replaces it. This package
is the step after that, when a program wants native values instead of canonical
JSON, bytes it can trust, and refusals it can act on.

**Implements Appendix N as of SpatialDDS-spec `9c3e1f2`.** Package versions and
specification versions move independently; `spatialdds.SPEC_COMMIT` is the one
that says what the wire looks like.

## What it is for: the lift

An endpoint serves Appendix N.1 canonical JSON, and three rules separate that
from the values a program wants. All three are easy to get subtly wrong:

* `int64` and `uint64` are **unconditional decimal strings**. A client that
  treats `stamp.sec` as a string gets surprising comparisons; one that calls
  `int()` on every string corrupts identifiers.
* An octet run is **base64**.
* A union is tagged on `"type"`, and carries `_discriminator` once lifted.

These are not this library's rules — they are the reference gateway's, and the
repository gates that the two agree by lifting every record in both committed
recordings with each and comparing. Same rules, same results, different source
of the type information: the gateway reads the IDL a recording carries, and a
client has no recording, so this reads the generated serializer tables that
travel with the package.

A lifted union keeps `_discriminator` rather than `"type"`, because that is
what the lift produces and inventing a fourth shape would be a fourth rule.
Pass `lifted=False` to `connect()` to get the canonical JSON untouched.

## The three bars

Each one runs on its own, pasted into a fresh shell with nothing else run
first, which is why the serve line all three need is printed here rather than
assumed. `gates/library.py` executes each bar against its own fresh endpoint
and compares its output to what is printed below it.

```sh
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap \
  --manifests samples/manifests --assets samples/assets \
  --publish --token not-a-secret --public-url https://demo.spatialdds.org
```

**Hello spatial, six lines.** Discover, subscribe, and read typed samples until
the latched set is complete.

<!-- bar: read -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787")
svc = ep.search(geohash="9v6krc")[0]
for ev in svc.topic("geo_anchor").subscribe(timeout=10):
    if isinstance(ev, sd.EndOfReplay): print("latched set complete:", ev.replayed); break
    print(" ", ev.key, ev.data["method"], ev.data["geopose"]["stamp"]["sec"])
```

```console
  anchor/fountain-north-rim Surveyed 1791559800
  anchor/fountain-south-rim VisualFix 1791559800
  anchor/fountain-east-rim VisualFix 1791559800
  anchor/fountain-west-rim VisualFix 1791559800
  anchor/fountain-plaque GNSS 1791559800
latched set complete: 5
```

`geopose.stamp.sec` arrives as an `int` there, not as `"1791559800"`. That is
the whole lift, in the one place a reader will notice it.

**Publish the duck, four lines.** The content manifest, and the announce that
places it.

<!-- bar: publish -->
```python
import json, spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787", token="not-a-secret")
d = json.load(open("samples/duck.input.json"))
print("manifest", ep.publish_manifest(d["manifest"]).created, "| announce", ep.publish(topic=d["announce"]["path"], key=d["announce"]["key"], record=d["announce"]["record"]).created)
```

```console
manifest True | announce True
```

**Resolve and verify, two lines.** The URI resolved per the endpoint's own
§7.5.2 metadata, and the asset's bytes checked against the hash the manifest
declared.

<!-- bar: resolve -->
```python
import json, spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787", token="not-a-secret"); ep.publish_manifest(json.load(open("samples/duck.input.json"))["manifest"]); print(len(ep.resolve("spatialdds://demo.spatialdds.org/south-mall/content/duck").fetch_asset()), "bytes verified")
```

```console
1432 bytes verified
```

The duck's manifest is published on the first line because these two lines
have to stand alone. Against an endpoint that already holds the manifest, that
call goes away and the bar is one line.

## The surface

| | |
| --- | --- |
| `sd.connect(base, token=None, verify_integrity=True, timeout=15.0, lifted=True)` | an `Endpoint`; nothing is fetched until you ask |
| `ep.search(geohash=None, kinds=None)` | §3.3.0 search → a list of `Hit` |
| `ep.near(lat, lon, radius_m)` | a helper over `search`, never a second protocol |
| `ep.resolve(uri)` | §7.5.2 resolution → `Resolved` |
| `ep.publish(topic=, key=, record=, if_match=None)` | PUT one instance → `Written` |
| `ep.publish_manifest(record)` | PUT a manifest, keyed by its own `id` |
| `ep.withdraw(topic=, key=)` | DELETE: leaves the latched set and disposes |
| `hit.service_id`, `.kind`, `.coverage`, `.topics` | the typed service manifest |
| `hit.topic(type_name)` | a `Topic`, URL taken from the manifest, never built |
| `hit.reaches(lat, lon, radius_m)` | does the declared coverage reach a point |
| `topic.type`, `.url`, `.name`, `.durability`, `.access`, `.fqn` | what the manifest said |
| `topic.latched()` | every live instance, lifted |
| `topic.instance(key)` | one instance, key percent-encoded per N.3 |
| `topic.subscribe(timeout=None)` | a generator of `Sample` / `Dispose` / `EndOfReplay` / `Liveliness` |
| `resolved.doc`, `.uri`, `.etag`, `.assets` | the resolved manifest |
| `resolved.asset_url(i)`, `.fetch_asset(i)` | the URL it would fetch, and the verified bytes |
| `written.doc`, `.etag`, `.created` | what a publish returned |
| `sd.lift(fqn, doc)` | the lift on its own, for JSON you already have |
| `sd.SPEC_COMMIT`, `sd.APPENDIX_N`, `sd.__version__` | what this speaks |

`ep.near()` picks a geohash precision whose cell covers the radius, encodes the
centre and eight points around the query circle so the covering cells are found
without neighbour arithmetic, searches each distinct cell, and filters the hits
by their own declared coverage. Everything it sends is an ordinary §3.3.0
geohash query.

## Errors

One family, built from the endpoint's own N.8 problem documents — `status`,
`type`, `detail` carried, not re-invented. Everything below derives from
`SpatialDDSError`, so one `except` catches the lot.

| | |
| --- | --- |
| `Unreachable` | the endpoint did not answer; names the URL and the serve line |
| `Unauthorized` | 401; says to pass `token=` and where the token is printed |
| `SurfaceOff` | 405 from a write verb; says to start with `--publish` |
| `Problem` | any other refusal, with `.status`, `.type`, `.slug`, `.detail` |
| `IntegrityError` | fetched bytes do not match the declared hash |
| `UnverifiableIntegrity` | the declared value is not a hash this library can check |

That last one is the unusual one and it is deliberate. SpatialDDS counts "a
checksum field in the body" as a §7.5.2 integrity signal, but nothing specifies
what such a field digests or in what form (spatialdds-web finding 31). Where a
declared value does not parse as a SHA-256 of the asset's bytes, this library
raises rather than reporting a check it did not perform.

## Integrity by default

Latched reads revalidate with the ETag the endpoint gave; `fetch_asset` checks
the bytes. Opting out exists and is loud: `verify_integrity=False`, at the call
site, nowhere else.

## Not in 0.1.0

Stated so nobody waits for them: no CLI, no async API, no caching beyond ETag
revalidation, no retry policy, no MCAP tooling, and no DDS dependency of any
kind. The wire is HTTP and a WebSocket; two dependencies, `requests` and
`websockets`, and nothing else.
