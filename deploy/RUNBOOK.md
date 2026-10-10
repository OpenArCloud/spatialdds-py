# Running the demo endpoint

One page. The endpoint serves the fountain recording read-only over the web
binding: five anchors, two content announces, one service announce, all three
resources public-cacheable.

Where it runs is not decided here, and nothing in this directory contacts any
infrastructure. The image has not been built — building it pulls a base image
and two wheels from public registries, which was out of scope for the batch
that wrote this. What a build would have caught was checked another way; see
"What was and was not verified" at the end.

## Build and run

```sh
docker build -f deploy/Dockerfile -t spatialdds-web .
docker run --rm -p 8787:8787 --read-only \
    -e SPATIALDDS_PUBLIC_URL=https://demo.spatialdds.org \
    spatialdds-web
```

| Knob | Default | What it is |
| --- | --- | --- |
| `SPATIALDDS_PORT` | `8787` | The port inside the container. |
| `SPATIALDDS_PUBLIC_URL` | `https://demo.spatialdds.org` | The URL clients reach the endpoint at. |
| `SPATIALDDS_MCAP` | the fountain recording | The recording to serve. |
| `SPATIALDDS_MANIFESTS` | `/srv/samples/manifests` | Content manifests this endpoint is the authority for. |

**`SPATIALDDS_PUBLIC_URL` is the one setting that will bite,** for two
reasons. A service manifest hands clients absolute URLs, and the gateway builds
them from this value: leave it unset behind a TLS terminator and the endpoint
answers every request correctly while advertising `http://0.0.0.0:8787/...` as
the address of each of its topics — healthy by every probe, and useless to every
client.

Second, §7.5.2 makes the authority in a `spatialdds://` URI a DNS name, so this
value decides whether the endpoint resolves URIs at all. The manifests it holds
belong to `demo.spatialdds.org`; deployed under any other name the endpoint
holds them and correctly refuses to resolve them, answering `404
not-an-authority` from the resolver path. That is conformant and it is not what
you want. The startup banner distinguishes the two cases in as many words:

```
  authority   demo.spatialdds.org, 2 manifest(s) resolvable
  authority   none: 2 manifest(s) held for demo.spatialdds.org, but this
              endpoint answers as '0.0.0.0:8787'; pass --public-url to serve
              resolution
```

Set it to the public origin, scheme included, no trailing slash.

No credentials. Everything this recording serves is public-cacheable, so there
is no bearer token to provision, rotate, or leak. `run_gateway.py` still
generates one at startup for the operator-scoped class; with this recording
nothing is in that class and the token gates nothing. If a future deployment
serves a recording with observations in it, that stops being true and the
token becomes a secret.

## Health check

There is no `/healthz`, and adding one would be worse than what is already
there:

```
GET /.well-known/spatialdds/search   ->  200
```

That path 200s only if the recording parsed, the state built, and the service
manifest serialised — which is the entire startup path. A dedicated liveness
route would answer 200 from a process that had failed to load the recording.
Point the load balancer at the discovery path.

An endpoint that is up but empty is the failure a bare status code hides, so
the check worth automating reads the body:

```sh
curl -s "$BASE/.well-known/spatialdds/search" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["results"]; assert len(r)==1 and len(r[0]["service"]["topics"])==3, r; print("ok: 1 service, 3 topics")'
```

## Post-deploy verification

Run from a checkout, not from inside the container:

```sh
python3 gates/content_resolution.py --remote
```

That follows the full reference chain — content announce to `manifest_uri` to
manifest to `assets[].uri` — and then fetches each asset's published URL and
byte-compares it against the committed file. It is the check that the assets
the endpoint points at actually exist at the addresses it publishes, which is
the one failure mode that only appears after a push.

What it does not do is read the deployed endpoint's own responses: it builds
its state from the local recording. Pair it with one request against the live
endpoint, which closes that gap:

```sh
curl -s "$BASE/ut-austin-south-mall/content/content_announce/v1" \
  | python3 -c 'import json,sys; [print(a["manifest_uri"]) for a in json.load(sys.stdin)]'
```

Both manifest URIs should appear, and both now resolve. Follow one the way a
client does — read the advertised `https_base`, then `GET` the URI against it:

```sh
RESOLVE=$(curl -s "$BASE/.well-known/spatialdds/resolver" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["https_base"])')
curl -sD - -o /dev/null --get "$RESOLVE" \
  --data-urlencode "uri=spatialdds://demo.spatialdds.org/south-mall/content/fountain-marker" \
  | tr -d '\r' | grep -iE '^(HTTP|cache-control|etag)'
```

A `200` with `Cache-Control: public, max-age=86400` — the manifest's own
`ttl_sec`, not the endpoint's 300-second default — and an `ETag` that is the
canonical digest. A `404 not-an-authority` here means
`SPATIALDDS_PUBLIC_URL` is wrong; see above. Mechanism and rationale in
[docs/RESOLUTION.md](../docs/RESOLUTION.md).

## Expected footprint

The smallest instance any provider sells. Measured on the image's own payload,
served by the clean dependency set the Dockerfile installs:

| | |
| --- | --- |
| Image payload | 1.1 MB (112 schemas, the 100 KB recording, two manifests, five Python modules) |
| Base image | `python:3.12-slim` plus `aiohttp` and `mcap` |
| Resident memory | 42.6 MB idle, 44.3 MB after 200 requests |
| Writable state | none; runs with `--read-only` and as a non-root user |
| Outbound network | none |
| Credentials | none |

The recording is baked into the image and the whole state is 8 latched
instances and 0 live events, built once at startup. There is nothing to scale
with traffic except the HTTP server itself, and every resource it serves
carries `Cache-Control: public, max-age=300`, so a CDN in front of it absorbs
repeat reads without the endpoint seeing them.

Rollback is `docker stop`. There is no state to migrate and nothing downstream
of it.

## What was and was not verified

Checked here, without a network:

- The `COPY` set is sufficient. The Dockerfile's copy lines were parsed and
  replicated into an otherwise empty tree, and the endpoint was served from
  that tree alone under a clean virtual environment holding only `aiohttp` and
  `mcap`: discovery 200, all 112 schemas listed and one fetched by the index's
  own URL with its digest, anchors 200, and a `spatialdds://` URI resolved 200
  through the advertised `https_base`.
- The startup banner reaches the log. Python block-buffers stdout when it is
  not a terminal, so every line of it used to sit in the buffer until the
  process exited — invisible under `docker logs`, which is the only place it
  would have been read. It is flushed before the event loop starts.
- The documented install line works from empty. `pip install requests
  websockets aiohttp mcap` into a fresh venv, then the examples run.
- `--public-url` rewrites every advertised URL, confirmed by reading the
  manifest back.

Not checked here: the image build itself, and therefore the base image tag, the
two wheel resolutions, and the `USER`/`--read-only` combination at runtime. The
first `docker build` is the remaining verification, and it needs a decision
about where this runs before it is worth spending.
