#!/usr/bin/env python3
"""Copy the fountain demo set into the distribution as package data.

    python3 tools/pack_demo.py [--check]

`python -m spatialdds.serve --demo` has to work from a wheel on a machine that
has never seen this repository, and package data has to live inside the
package directory. So the fountain set exists twice: the authored original
under `samples/`, written by `tools/author_anchors.py`, and this copy under
`spatialdds/demo/`.

Two copies of a file is two answers waiting to happen, which is why this is a
tool and not a habit: re-authoring the recording means running this, and
`gates/serve_demo.py` fails if the two ever differ. `--check` reports without
writing, which is what the gate calls.

The retail recording is deliberately not packaged. It is 1.4 MB of captured
traffic that belongs to this repository's evidence, not to a client library.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "samples"
DST = ROOT / "lib" / "spatialdds" / "spatialdds" / "demo"

#: Source path -> packaged path, relative to each root. The fountain set and
#: nothing else: a recording, the manifests it refers to, and the asset bytes
#: those manifests name.
FILES = {
    "fountain-anchors.mcap": "fountain-anchors.mcap",
    "manifests/content-fountain-label.json": "manifests/content-fountain-label.json",
    "manifests/content-fountain-marker.json": "manifests/content-fountain-marker.json",
    "assets/fountain-label.txt": "assets/fountain-label.txt",
    "assets/fountain-marker.gltf": "assets/fountain-marker.gltf",
    "assets/duck.gltf": "assets/duck.gltf",
}


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def compare() -> list[tuple[str, str]]:
    """`(packaged path, what is wrong)` for every file that is not current."""
    bad = []
    for src, dst in sorted(FILES.items()):
        s, d = SRC / src, DST / dst
        if not s.is_file():
            bad.append((dst, f"the source {s.relative_to(ROOT)} is missing"))
        elif not d.is_file():
            bad.append((dst, "not packaged"))
        elif digest(s) != digest(d):
            bad.append((dst, f"differs from {s.relative_to(ROOT)}"))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report drift without writing anything")
    a = ap.parse_args()
    bad = compare()
    if a.check:
        for dst, why in bad:
            print(f"  STALE {dst}: {why}")
        print(f"{len(FILES) - len(bad)}/{len(FILES)} packaged demo file(s) "
              f"current")
        return 1 if bad else 0
    total = 0
    for src, dst in sorted(FILES.items()):
        s, d = SRC / src, DST / dst
        if not s.is_file():
            raise SystemExit(f"no such source: {s}")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        total += d.stat().st_size
        print(f"  {d.relative_to(ROOT)}  {d.stat().st_size} bytes  "
              f"sha256 {digest(d)[:16]}…")
    print(f"\n{len(FILES)} file(s), {total} bytes packaged as "
          f"spatialdds/demo/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
