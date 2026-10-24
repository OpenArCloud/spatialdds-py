#!/bin/bash
# Run one torture script in a container shaped like the box: 512 MB of
# memory, the checkout's package installed from source, and (for disk_full) a
# 1 MB tmpfs for the journal.
#     tools/torture/run.sh disk_full | torn_write [N] | soak [N]
set -euo pipefail
here=$(cd "$(dirname "$0")/../.." && pwd)
name=${1:?disk_full | torn_write | soak}; shift
exec docker run --rm --memory 512m --memory-swap 512m \
    --tmpfs /small-fs:size=1m \
    -v "$here:/repo:ro" python:3.12-slim bash -c "
        cp -r /repo /w && cd /w && rm -rf lib/spatialdds/build lib/spatialdds/*.egg-info &&
        pip -q --disable-pip-version-check install ./lib/spatialdds[server] >/dev/null 2>&1 &&
        python3 tools/torture/$name.py $( [ $name = disk_full ] && echo /small-fs ) $*"
