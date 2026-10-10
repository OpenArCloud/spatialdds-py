# A read-only SpatialDDS web-binding endpoint serving the fountain recording.
#
# Everything this endpoint serves is public-cacheable, which decides the whole
# shape of the deployment: no credentials, no writable state, no outbound
# calls. The recording is baked into the image, so the running container has
# nothing to mount and nothing to lose.
#
#   docker build -f deploy/Dockerfile -t spatialdds-web .
#   docker run --rm -p 8787:8787 --read-only \
#       -e SPATIALDDS_PUBLIC_URL=https://demo.spatialdds.org \
#       spatialdds-web
#
# See deploy/RUNBOOK.md. Nothing here contacts any infrastructure; building
# the image is the whole of it.

FROM python:3.12-slim

# aiohttp serves it, mcap reads the recording. The XCDR2 codec, the JSON
# mapping and the geohash arithmetic are this repository's own pure Python, so
# there is no build toolchain and no native dependency to pin.
RUN pip install --no-cache-dir aiohttp==3.* mcap==1.*

WORKDIR /srv
COPY spatialdds_web/        /srv/spatialdds_web/
COPY tools/run_gateway.py   /srv/tools/run_gateway.py
COPY webjson/schemas/       /srv/webjson/schemas/
COPY webjson/serializer_tables.json webjson/PROVENANCE  /srv/webjson/
COPY samples/fountain-anchors.mcap  /srv/samples/fountain-anchors.mcap
# The content manifests this endpoint is the authority for. §7.5.2 resolution
# answers only for what is held here, keyed by each manifest's own `id`.
COPY samples/manifests/            /srv/samples/manifests/

# The build context carries whatever a developer's checkout happens to hold;
# byte-compiled caches are not part of the artifact.
RUN find /srv -name __pycache__ -type d -prune -exec rm -rf {} +

# Nothing in the image is written to at runtime, so it runs unprivileged
# against a read-only root filesystem.
RUN useradd --no-create-home --shell /usr/sbin/nologin spatialdds
USER spatialdds

ENV SPATIALDDS_MCAP=/srv/samples/fountain-anchors.mcap \
    SPATIALDDS_MANIFESTS=/srv/samples/manifests \
    SPATIALDDS_PORT=8787 \
    SPATIALDDS_PUBLIC_URL=https://demo.spatialdds.org
EXPOSE 8787

# --host 0.0.0.0 binds the container's interface; --public-url is the address
# clients actually reach, which is what every URL in a served service manifest
# is built from. Behind a TLS terminator the two always differ, and getting it
# wrong fails quietly: the endpoint answers and the manifest it serves points
# at an address no client can follow.
#
# It is now load-bearing for a second reason. §7.5.2 makes the authority in a
# `spatialdds://` URI a DNS name, so the endpoint offers resolution only when
# --public-url names the authority its manifests belong to. That is why this
# image defaults it rather than leaving it empty: a deployment under any other
# name holds the manifests and correctly refuses to resolve them, and the
# startup banner says so in as many words.
CMD ["sh", "-c", "exec python3 tools/run_gateway.py \
  --mcap \"$SPATIALDDS_MCAP\" --manifests \"$SPATIALDDS_MANIFESTS\" \
  --host 0.0.0.0 --port \"$SPATIALDDS_PORT\" \
  ${SPATIALDDS_PUBLIC_URL:+--public-url \"$SPATIALDDS_PUBLIC_URL\"}"]
