# spatialdds

SpatialDDS over plain HTTPS and a WebSocket: a client for programs, and the
reference registry behind one command.

```sh
pip install spatialdds                       # the client: two dependencies
pip install "spatialdds[server]"             # and the registry
pip install "./lib/spatialdds[server]"       # from a checkout of this repo
```

`fetch` and a WebSocket really are the whole client -- the endpoint's own
README shows that in three lines of `curl`, and nothing here replaces it. This
package is the step after that, when a program wants native values instead of
canonical JSON, bytes it can trust, refusals it can act on, and records it did
not have to assemble member by member.

**Implements Appendix N as of SpatialDDS-spec `9c3e1f2`.** Package versions and
specification versions move independently; `spatialdds.SPEC_COMMIT` is the one
that says what the wire looks like.

The source, the reference gateway, the recordings and every gate behind the
claims on this page are at [https://github.com/OpenArCloud/spatialdds-web](https://github.com/OpenArCloud/spatialdds-web). Every code block below is executed
by one of those gates on every run, against an endpoint started for it.

## Your own registry, in one command

```sh
python -m spatialdds.serve --authority registry.mytown.example --publish
```

That is a conformant SpatialDDS endpoint: empty, typed, and ready to be
published into. Its types come from the IDL pinned inside this package at
`SPEC_COMMIT`, so there is no base recording to find first -- the specification
is the type source, and `spatialdds.server.pin` is where it lives. State goes
to a journal (`./spatialdds-registry.mcap` by default, named in the banner)
which is an ordinary SpatialDDS recording: restart replays it, and any tool
that reads a recording can read your registry's state.

`python -m spatialdds.serve --demo` serves the packaged fountain set instead --
five surveyed anchors, two content manifests, the assets they name -- with
publishing on and a token printed. Same boot path, seed data on top.

The server half is an extra because being an endpoint is the part you opt
into: `aiohttp` serves it, `mcap` is the journal, `jsonschema` validates every
published body before it reaches the wire. Reading somebody else's registry
needs none of them.

## Publishing without writing out eighteen members

Appendix N's schemas make every member of a record required, so an `Announce`
is eighteen members, a `ContentAnnounce` sixteen, a `GeoAnchor` nine. The
builders take the arguments you actually have:

<!-- bar: verbs -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787", token="not-a-secret", scene="mytown")
a = ep.publish_service(service_id="mytown-vps", kind="VPS",
                       coverage=sd.circle(30.2849, -97.7393, 500))
b = ep.publish_content(
    content_id="duck", title="A duck", position=(30.2849, -97.7393),
    asset=sd.asset_ref("https://cdn.example/duck.gltf",
                       media_type="model/gltf+json"))
c = ep.publish_anchor("anchor/duck-perch", lat=30.2849, lon=-97.7393,
                      method="VisualFix", sigma_enu_m=(0.25, 0.25, 0.5),
                      content="duck")
print("service", a.created, "| content", b.created, "| anchor", c.created)
print("manifest", b.manifest.doc["id"])
```

```console
service True | content True | anchor True
manifest spatialdds://demo.spatialdds.org/mytown/content/duck
```

`publish_service` and `publish_content` are two PUTs each -- the manifest
first, then the record that points at it -- and return what `publish` returns,
with the manifest's own result on `.manifest`.

A full service contract is the same one call, with the claims stated:

<!-- bar: contract -->
```python
import spatialdds as sd
ep = sd.connect("http://127.0.0.1:8787", token="not-a-secret", scene="mytown")
area = sd.circle(30.2849, -97.7393, 500)
w = ep.publish_service(
    service_id="mytown-vps", kind="VPS", coverage=area,
    caps=sd.capabilities(
        supported=[sd.profile("spatial.core", minor=8)],
        features=["web-binding/json"]),
    manifest=sd.service_manifest(
        "mytown-vps", "VPS", authority=ep.authority, scene="mytown",
        profiles=["Core", "AR+Geo"],
        request={"features_topic": "feat.keyframe"},
        response={"minimal": "geo.fix"},
        limits={"max_fps": 10},
        auth={"scheme": "bearer",
              "issuer": "https://registry.mytown.example"},
        coverage=area, cells=sd.cells(area)))
print("profiles", w.manifest.doc["profiles"])
print("limits  ", w.manifest.doc["limits"])
```

```console
profiles ['Core', 'AR+Geo']
limits   {'max_fps': 10}
```

Every one of those is something a service operator knows and a library cannot
guess. [The mytown walkthrough](https://github.com/OpenArCloud/spatialdds-web/blob/main/docs/MYTOWN.md) publishes that
and prints the whole served document;
[`gates/builders.py`](https://github.com/OpenArCloud/spatialdds-web/blob/main/gates/builders.py) rebuilds that
repository's hand-authored `austin-vps` and duck records through these calls
and compares them member by member, which is what makes "every member is
reachable from an argument" a check rather than a claim.

**Two forms for an asset, because there are two situations.**
`sd.asset(path_or_bytes, media_type=...)` reads the data and computes the hash
and the byte count itself: a hash nobody typed is a hash a reader can trust.
`sd.asset_ref(url, hash=..., media_type=...)` points at something somebody
else hosts, and the hash comes from whoever has the bytes. Pass no hash and
the reference carries no integrity claim, which costs a reader one thing:
`fetch_asset()` raises rather than reporting a check it could not perform
([finding 31](https://github.com/OpenArCloud/spatialdds-web/blob/main/WEB-BINDING-FINDINGS.md)).
`publish_content` takes either, or a path, or bytes, or a URL.

The builders are also usable on their own: `sd.service_announce`,
`sd.service_manifest`, `sd.content_announce`, `sd.content_manifest`,
`sd.geo_anchor`, with `sd.circle`, `sd.bbox`, `sd.geopose`, `sd.cov_pos3`,
`sd.cov_none`, `sd.profile`, `sd.capabilities`, `sd.asset_ref`,
`sd.frame`,
`sd.earth_fixed`, `sd.stamp`, `sd.topic`, `sd.asset`, `sd.cells`,
`sd.service_uri` and `sd.content_uri`. Each returns a plain dict in canonical
form, publishable as it stands and dumpable as the authoring JSON this
repository's tools read. `spatialdds.records` is the module they live in.

**Conventions fill plumbing. They never fill claims.** A stamp of now, an
earth-fixed frame, a checksum computed from the record itself: plumbing, with
one sensible value each, and filling them saves you writing out what you had
no choice about. A profile list, a rate limit, a request topic, an auth
scheme, a title: claims, and no default for any of them is honest. State
nothing and you publish a minimal record, which is *correct*; state a
contract and you publish it in one call. There is nothing in between for a
library to invent.

**What they fill in, and what they refuse to.** The conventions above are
documented where they are applied: a stamp of now unless you pass `when=`, an
earth-fixed frame unless you pass one, and URIs assembled under a stated
authority -- never a guessed one. `ep.publish_*` gets the authority from the
endpoint itself (`ep.authority`, which asks the resolver metadata, then the
endpoint's own 404 document at `/`, then the address you connected to). What
they never fill is **identity**: `provider_id` and `org` are yours to state,
because a library that invented a provider id would be inventing provenance.

`GeoAnchor.checksum` is required by 1.8 and specified by nobody
(spatialdds-web finding 31). The builder fills it with the SHA-256 of the
record's own canonical JSON with `checksum` emptied -- a convention, chosen
because a reader can recompute it from what was served. If the specification
settles the semantics, this changes.

The full map of builder argument to IDL member, including every member filled
by convention, is printed by `python3 gates/builders.py --census` in the
repository.

## What it is for: the lift

An endpoint serves Appendix N.1 canonical JSON, and three rules separate that
from the values a program wants. All three are easy to get subtly wrong:

* `int64` and `uint64` are **unconditional decimal strings**. A client that
  treats `stamp.sec` as a string gets surprising comparisons; one that calls
  `int()` on every string corrupts identifiers.
* An octet run is **base64**.
* A union is tagged on `"type"`, and carries `_discriminator` once lifted.

These are not this library's rules -- they are the reference gateway's, and
the repository gates that the two agree by lifting every record in both
committed recordings with each implementation and comparing. Same rules, same
results, different source of the type information: the server reads the IDL,
and a client reading a remote endpoint has none, so this reads the generated
serializer tables that travel with the package. That gate earned its keep on
2026-10-15, when the disagreement it found turned out to be in the server.

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
svc = next(h for h in ep.search(geohash="9v6krc") if h.serves("geo_anchor"))
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

| call | what it does |
| --- | --- |
| `sd.connect(base, token=None, verify_integrity=True, timeout=15.0, lifted=True, scene="default", authority=None)` | an `Endpoint`; nothing is fetched until you ask |
| `ep.search(geohash=None, kinds=None)` | §3.3.0 search → a list of `Hit` |
| `ep.near(lat, lon, radius_m)` | a helper over `search`, never a second protocol |
| `ep.resolve(uri)` | §7.5.2 resolution → `Resolved` |
| `ep.authority` | the DNS name the endpoint says it answers as |
| `ep.publish(topic=, key=, record=, if_match=None)` | PUT one instance → `Written` |
| `ep.publish_manifest(record)` | PUT a manifest, keyed by its own `id` |
| `ep.publish_service(service_id=, kind=, coverage=, manifest=None, ...)` | build and PUT an announce, and its manifest if given |
| `ep.publish_content(content_id=, position=, asset=, ...)` | build and PUT a content manifest and its announce |
| `ep.publish_anchor(anchor_id, lat=, lon=, method=, content=None, ...)` | build and PUT an anchor, optionally naming content |
| `ep.withdraw(topic=, key=)` | DELETE: leaves the latched set and disposes |
| `hit.service_id`, `.kind`, `.coverage`, `.topics` | the typed service manifest |
| `hit.serves(type_name)` | does this service offer a topic of that type? for picking one out of a search result |
| `hit.topic(type_name)` | a `Topic`, URL taken from the manifest, never built |
| `hit.reaches(lat, lon, radius_m)` | does the declared coverage reach a point |
| `topic.type`, `.url`, `.name`, `.durability`, `.access`, `.fqn` | what the manifest said |
| `topic.latched()` | every live instance, lifted |
| `topic.instance(key)` | one instance, key percent-encoded per N.3 |
| `topic.subscribe(timeout=None)` | a generator of `Sample` / `Dispose` / `EndOfReplay` / `Liveliness` |
| `resolved.doc`, `.uri`, `.etag`, `.assets` | the resolved manifest |
| `resolved.asset_url(i)`, `.fetch_asset(i)` | the URL it would fetch, and the verified bytes |
| `written.doc`, `.etag`, `.created`, `.manifest` | what a publish returned |
| `sd.service_manifest(service_id, kind, authority=, scene=, profiles=, request=, response=, limits=, auth=, coverage=, cells=)` | the contract behind an announce; every member a claim, so every member an argument |
| `sd.capabilities(supported=, preferred=, features=)`, `sd.profile(name, minor=)` | what a service supports, for `service_announce(caps=)` |
| `sd.asset(path_or_bytes, media_type=)` / `sd.asset_ref(url, hash=, media_type=)` | an asset hashed from its bytes, or a reference to one hosted elsewhere |
| `sd.records` | the builders, listed above |
| `sd.lift(fqn, doc)` | the lift on its own, for JSON you already have |
| `sd.SPEC_COMMIT`, `sd.APPENDIX_N`, `sd.__version__` | what this speaks |

`ep.near()` picks a geohash precision whose cell covers the radius, encodes the
centre and eight points around the query circle so the covering cells are found
without neighbour arithmetic, searches each distinct cell, and filters the hits
by their own declared coverage. Everything it sends is an ordinary §3.3.0
geohash query.

`topic.url` and `assets[].uri` are followed by different rules, deliberately. A
topic URL comes out of a service manifest this endpoint served, so its path is
this endpoint's and the library follows it by path -- which is what makes a
registry advertising a DNS name usable from a laptop. An asset URI is an
ordinary web URL that usually points somewhere else entirely, so it is fetched
exactly as written; the one exception is an asset on the endpoint's own
advertised authority, which is rebased onto the endpoint you are talking to.

## Errors

One family, built from the endpoint's own N.8 problem documents — `status`,
`type`, `detail` carried, not re-invented. Everything below derives from
`SpatialDDSError`, so one `except` catches the lot.

| exception | raised when |
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

## New in 0.4.0: a command surface on the server half

`spatialdds[server]` now implements Appendix N.6. A command is a POST to the
lane that accepts one, and an embedder registers what a verb means:

| Part | What it is |
| --- | --- |
| the seam | `spatialdds.server.commands.Registry().register(fqn, handler)` |
| a handler gets | the validated, lifted record — the same JSON a GET would serve |
| a handler returns | `Outcome.accept()` or `Outcome.decline("why")` |
| the wire | `202` accepted, `409` declined with the reason, `405` where no handler is registered |

`python -m spatialdds.serve --demo` registers one so the surface is reachable
without writing a backend. An endpoint with no handlers is northbound-only and
every command POST is a clean `405`, which is what N.6 requires and what every
earlier version did.

**The client half has no command method.** This release adds the surface, not
a helper for calling it — `requests.post(topic_url, json=record)` with the
operator token is the whole of it, and `docs/WIRE.md` documents the response
shapes. Said plainly because "commands are supported" invites a reader to look
for `hit.command(...)`, and there isn't one.

## New in 0.5.0: embedding the gateway against your own live source

0.4.0 let a backend own the verbs. 0.5.0 lets it own the data, which took two
changes found by doing it:

| Call | What it does |
| --- | --- |
| `publish.apply_sample(st, topic, res, key, data)` | what a **new** subscriber replays, and what the collection GET returns |
| `gw_http.notify(app, topic, event)` | wakes the subscribers **already** connected |

They are halves of one contract. Until 0.5.0 only the first was public, so an
embedder could update the latched set and reach every client except the ones
currently watching — measured as three seconds of silence on an open socket,
with the same event arriving instantly once it was handed to the queue by
hand.

And `make_app` has a second switch, because it used to have one doing two
jobs:

| Argument | What it controls |
| --- | --- |
| `publish_enabled` | the PUT/DELETE write surface, and nothing else |
| `live_subscriptions` | whether an N.5 subscription stays open past the end of a recorded stream |

`live_subscriptions` defaults to following `publish_enabled`, so nothing that
already worked changed. An embedder with its own source passes
`publish_enabled=False, live_subscriptions=True` and gets the live lane with
no write surface at all — which was unreachable before, when keeping
subscribers connected meant exposing writes.

0.5.1 fixes a defect in 0.4.0's seam that only its first real consumer could
find: `make_app(command_registry=...)` silently replaced a registry that was
empty at the time, because `Registry` was falsy when empty and the assignment
used `or`. Since an embedder must have the app before it can build the backend
that supplies the handlers, passing an empty registry and filling it a few
lines later is the normal shape — and every one of those registrations went to
an object the app had already discarded. If you called `register()` before
`make_app()`, 0.4.0 and 0.5.0 worked; if you called it after, the command lane
advertised nothing and refused every POST.

0.5.2 made `notify` safe from the thread its own docstring invited. It put
onto the subscriber queue directly, and `asyncio.Queue.put_nowait` from a
foreign thread enqueues without waking the loop — so a backend reading a bus
in a thread fed a parked subscriber nothing until the loop woke for some other
reason. Busy endpoint, invisible; idle endpoint, silent. Measured: a bridge
pumping 202 samples into two registered subscriber queues delivered 0 live
events. If you call `notify` only from the serving loop, 0.5.0 and 0.5.1 were
correct for you.

0.5.3 lets a browser reach this gateway at all. `Authorization` is not a
CORS-safelisted request header, so a cross-origin request carrying one is
preceded by an `OPTIONS` preflight — and no `OPTIONS` was routed, so aiohttp
answered `405 Allow: GET,HEAD,POST` and the browser stopped there without
making the request. Two consequences, both found from outside by a real page
rather than by a gate here: **N.6 was unreachable from a browser for every
access class**, because a command always carries a credential and so always
preflights, which no reclassification can change; and a `public-cacheable`
`GET` that answers `200` with `Access-Control-Allow-Origin: *` failed outright
if the client volunteered a credential it did not need.

An authorized operator-scoped response now also carries the asking origin,
echoed, with `Vary: Origin, Authorization`. The previous reasoning was that a
gated resource should never be CORS-open because its credential "must not be
invited to attach ambiently" — right about the risk and wrong about the
mechanism. `Access-Control-Allow-Credentials` is what issues that invitation,
this package sets it nowhere, and a `Bearer` token the page holds and chooses
to attach is the opposite of ambient. Refusing the origin never kept a
credential out; it made an authorized `200` unreadable by the client that had
authorized itself. The body is still `no-store`, the credential is still
required, and nothing is newly reachable without one.

If your clients are not browsers, 0.5.0 through 0.5.2 were correct for you:
nothing else changes, and no response loses a header it had.

## Not in 0.5.3

Stated so nobody waits for them: no async API, no caching beyond ETag
revalidation, no retry policy, no MCAP tooling beyond the journal the server
writes, no client-side command helper, and no DDS dependency of any kind. The
wire is HTTP and a WebSocket.

0.4.0 added the N.6 command surface described above; 0.5.0 made it usable
from an embedder with a live source, which is two releases for one capability
and the honest shape of finding the second half by using the first.

0.3.1 was this page: two code blocks on it had no gate behind them and both
were broken, and a long description is baked into the artifact, so the page on
PyPI only heals with a release. Every block here is executed by a gate now,
and the gate also refuses a relative link, an empty table header and a serve
line naming a flag that does not exist.

`sd.asset` changed meaning in 0.3.0, which is why that version moved: it now
reads bytes and computes a hash, where in 0.2.0 it took a URL and a hash you
supplied. That second form is `sd.asset_ref`. A published release has to stay
reproducible from a public commit, so the API could not change under the same
number.

On the registry, four things are deferred rather than missing, and the same
four are on the 1.9 candidates list: **leases** (nothing expires; `ttl_sec` is
advice to caches), **per-provider scopes** (one token, one operator),
**server-set identity** (`provider_id` is whatever the publisher says), and
**upstream subscriptions** (a registry does not follow another registry).
