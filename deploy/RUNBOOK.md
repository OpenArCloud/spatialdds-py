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
docker build -f deploy/Dockerfile -t spatialdds-py .
docker run --rm -p 8787:8787 --read-only \
    -e SPATIALDDS_AUTHORITY=demo.spatialdds.org \
    spatialdds-py
```

The image changed shape in 0.2.0 and got smaller: it installs the
distribution from the build context -- `pip install "./lib/spatialdds[server]"`
-- and serves `python -m spatialdds.serve --demo --no-publish`. The recording,
the manifests, the assets and the 112 schemas all travel inside the package,
so there is nothing else to copy in. `--no-publish` keeps the write surface
off, which is what lets the container run with a read-only root filesystem and
no journal.

| Knob | Default | What it is |
| --- | --- | --- |
| `SPATIALDDS_PORT` | `8787` | The port inside the container. |
| `SPATIALDDS_AUTHORITY` | `demo.spatialdds.org` | The DNS name clients reach the endpoint at. |

Two knobs where there were four: the recording and the manifests are no longer
paths to set, because `--demo` serves the set packaged inside the distribution.
`SPATIALDDS_AUTHORITY` replaces `SPATIALDDS_PUBLIC_URL` and takes a name
rather than a URL -- `python -m spatialdds.serve --public-url` still exists for
a deployment behind an unusual scheme or path prefix.

**`SPATIALDDS_AUTHORITY` is the one setting that will bite,** for two
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

**The publish surface is off here, and nothing in `deploy/` turns it on.** The
write verbs answer `405` and the endpoint's read-only, credential-irrelevant
posture is unchanged; see [docs/PUBLISH.md](../docs/PUBLISH.md) if you want it
somewhere else.

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
  --data-urlencode "uri=spatialdds://demo.spatialdds.org/ut-austin-south-mall/content/fountain-marker" \
  | tr -d '\r' | grep -iE '^(HTTP|cache-control|etag)'
```

A `200` with `Cache-Control: public, max-age=86400` — the manifest's own
`ttl_sec`, not the endpoint's 300-second default — and an `ETag` that is the
canonical digest. A `404 not-an-authority` here means
`SPATIALDDS_AUTHORITY` is wrong; see above. Mechanism and rationale in
[docs/RESOLUTION.md](../docs/RESOLUTION.md).

## Upgrading across 0.7.3: changed ETags on unchanged content

An endpoint built on 0.7.2 or earlier that moves to 0.7.3 or later serves
the earth-fixed frame with a uuid, `00cc62ae-c21e-5d3b-b5f9-2587a084ba31`,
where it served `""` (finding 70, S14: Appendix G makes the uuid a frame's
identity). The gateway's own manifest coverage and anything the regenerated
seed builds (announces and manifests) change bytes, so their ETags change.
Every cached client revalidates once and gets a `200` where it got a `304`.
That is expected and harmless. Do not read changed ETags on unchanged
content as a defect in that deploy.

The journal's stored records keep their bytes, because the gateway serves
what was published. Published content is unaffected.

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

### Rebuilding for a CA change

The image's trust store is baked in. `deploy/Dockerfile` refreshes pip and
certifi before it installs anything — GHSA-6q48-6c7w-9gw2 (Critical), where
certifi before 2026.10.19 bundled `emSign ECC Root CA - G3` and pip verified
TLS against it — but **layers are immutable, so a tag keeps whatever it was
built with.** A certifi advisory therefore needs a rebuild and a redeploy,
not a restart; a running container cannot pick one up.

Two details that cost time if assumed the other way. The fix is 2026.10.19,
not 2026.10.18: the earlier release removed every emSign root and the later
one restored the unaffected ones, so a *patched* bundle still contains three
of them and the only meaningful check is whether `emSign ECC Root CA - G3`
specifically is present. And `pip install -U certifi` does not fix pip, which
verifies against its own vendored copy at `pip._vendor.certifi` and moves
only when pip does — which is why the line upgrades both.

`gates/deploy_image.py` asserts the prelude is the image's *first* pip
install, so an edit that puts a fetch in front of it fails there.

## Releasing the `spatialdds` package

Separate from the endpoint, and separate from this repository's own version:
`lib/spatialdds` is published to PyPI from a tag, by the trusted-publishing
workflow in `.github/workflows/publish-spatialdds.yml`. The workflow holds no
credential — PyPI must have a trusted publisher pointing at this repository,
that filename and the `pypi` environment, which is a one-time setup on
pypi.org and the only reason the workflow is inert as committed.

Five lines, from a commit whose gates are green:

1. `python3 gates/run_all.py` — 47 gates, 0 failed. A release is cut from a
   verified commit, which is why the workflow does not re-run them itself.
2. Bump `__version__` in `lib/spatialdds/spatialdds/__init__.py`, and
   `version` in `lib/spatialdds/pyproject.toml`, to the same number.
3. `python3 gates/library.py` — it checks that those two agree, that the
   README names the spec commit the module claims, and that the package
   imports with nothing from this repository on the path.
4. Commit, then `git tag spatialdds-v<version> && git push origin
   spatialdds-v<version>`. The workflow's build job fails if the tag and the
   package disagree, so a mistyped tag does not reach PyPI.
5. Approve the `pypi` environment if the repository is set to require it, then
   confirm the upload: `pip download spatialdds==<version> --no-deps` into an
   empty directory.
6. **Check that what was published is what the pushed commit builds.** Name and
   size are not enough, and whole-file hashes are worse than nothing here: a
   wheel is not bit-reproducible across build-tool versions, so comparing
   whole files reports a difference for every release built on a different
   machine and trains you to ignore it.

   ```sh
   git archive <pushed commit> | (mkdir -p /tmp/t && cd /tmp/t && tar -xf -)
   (cd /tmp/t/lib/spatialdds && python3 -m pip wheel . --no-deps \
       --no-build-isolation -w /tmp/t/dist)
   python3 tools/compare_wheel.py <downloaded>.whl /tmp/t/dist/*.whl
   ```

   It compares every payload file by SHA-256 and diffs `METADATA`, `WHEEL` and
   `RECORD` line by line. **Any payload difference is a different artifact**,
   and the answer to one is a new patch version cut from the pushed tree, not
   an explanation. `dist-info` lines are expected to differ and are printed so
   you can read them.

   Run on the real pair on 2026-10-16: PyPI's `spatialdds 0.2.0` against a
   wheel built from public commit `5282a02` gave *139 payload files, 0
   differing*, with `METADATA` differing by one line of 294
   (`Metadata-Version: 2.4` against `2.2`) and `WHEEL` by one
   (`setuptools (84.0.0)` against `(75.8.0)`). That is what a traceable
   release looks like.

   `python3 tools/compare_wheel.py --self-test` checks the tool's own three
   verdicts offline, and `gates/library.py` runs that on every gate run, so the
   tool is not meeting its first real difference untested.

`SPEC_COMMIT` in the package is the commit of SpatialDDS-spec whose Appendix N
the release implements, and it moves on its own schedule: a package release
does not imply a specification release, and the version numbers are unrelated
by design.

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
  websockets aiohttp mcap` into a fresh venv, then the examples run. As of
  0.2.0 `gates/quickstart.py` goes further and performs the install every
  walked page names -- from the checkout, with `--no-index` -- so the pages'
  own install lines are executed rather than read (finding 38).
- `--public-url` rewrites every advertised URL, confirmed by reading the
  manifest back.

Not checked here: the image build itself, and therefore the base image tag, the
two wheel resolutions, and the `USER`/`--read-only` combination at runtime. The
first `docker build` is the remaining verification, and it needs a decision
about where this runs before it is worth spending.
