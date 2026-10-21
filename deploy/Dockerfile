# A read-only SpatialDDS web-binding endpoint serving the fountain recording.
#
# Everything this endpoint serves is public-cacheable, which decides the whole
# shape of the deployment: no credentials, no writable state, no outbound
# calls. The recording is baked into the image -- it travels inside the
# `spatialdds` package as its demo data -- so the running container has
# nothing to mount and nothing to lose.
#
# Note that `--demo` switches the write surface on when run from a terminal.
# This CMD passes --no-publish to keep it off, because a public endpoint is
# not somebody's first two minutes with the thing.
#
#   docker build -f deploy/Dockerfile -t spatialdds-web .
#   docker run --rm -p 8787:8787 --read-only \
#       -e SPATIALDDS_AUTHORITY=demo.spatialdds.org \
#       spatialdds-web
#
# See deploy/RUNBOOK.md. Nothing here contacts any infrastructure; building
# the image is the whole of it.

FROM python:3.12-slim

# One install line, because 0.2.0 moved the gateway into the distribution:
# the package carries the server, the generated artifacts it serves, the
# pinned IDL, and the fountain demo set. The `server` extra brings aiohttp,
# mcap and jsonschema; the XCDR2 codec, the JSON mapping and the geohash
# arithmetic are pure Python in the package itself, so there is no build
# toolchain and no native dependency to pin.
#
# Installed from the build context rather than from PyPI on purpose. The image
# then holds exactly the code in this commit, with no network dependency at
# build time and no question about which release it picked up.
WORKDIR /srv
COPY lib/spatialdds/ /srv/lib/spatialdds/
# certifi before the first fetch: GHSA-6q48-6c7w-9gw2 (Critical).
# pip verifies TLS against a certifi bundle -- its own vendored one,
# which moves only when pip does -- so the install below would
# otherwise run against whatever the base image shipped.
#
# **This does not reach an image that is already built.** Layers are
# immutable, so the deployed endpoint picks it up only on a rebuild
# and redeploy; see deploy/RUNBOOK.md.
RUN pip install --no-cache-dir -U pip certifi
RUN pip install --no-cache-dir "/srv/lib/spatialdds[server]" && \
    rm -rf /srv/lib

# The build context carries whatever a developer's checkout happens to hold;
# byte-compiled caches are not part of the artifact.
RUN find /srv -name __pycache__ -type d -prune -exec rm -rf {} +

# Nothing in the image is written to at runtime, so it runs unprivileged
# against a read-only root filesystem.
RUN useradd --no-create-home --shell /usr/sbin/nologin spatialdds
USER spatialdds

ENV SPATIALDDS_PORT=8787 \
    SPATIALDDS_AUTHORITY=demo.spatialdds.org
EXPOSE 8787

# --demo serves the packaged fountain set, which is byte-identical to the
# repository's `samples/` copy and is why this image needs no samples of its
# own. --host 0.0.0.0 binds the container's interface; --authority is the DNS
# name clients reach, which is what every URL in a served service manifest is
# built from. Behind a TLS terminator the two always differ, and getting it
# wrong fails quietly: the endpoint answers and the manifest it serves points
# at an address no client can follow.
#
# --authority is load-bearing for a second reason. §7.5.2 makes the authority
# in a `spatialdds://` URI a DNS name, so the endpoint offers resolution only
# when it answers as the authority its manifests belong to. That is why this
# image defaults it rather than leaving it empty: a deployment under any other
# name holds the manifests and correctly refuses to resolve them, and the
# startup banner says so in as many words.
#
# --no-publish, because --demo turns the write surface on for somebody's
# first two minutes and a public deployment is not that. With it off the
# container needs no writable filesystem and no journal, which is what lets it
# run --read-only.
CMD ["sh", "-c", "exec python3 -m spatialdds.serve --demo --no-publish \
  --host 0.0.0.0 --port \"$SPATIALDDS_PORT\" \
  ${SPATIALDDS_AUTHORITY:+--authority \"$SPATIALDDS_AUTHORITY\"}"]
