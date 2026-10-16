#!/usr/bin/env python3
"""Serve a SpatialDDS recording over the web binding, from this checkout.

    python3 tools/run_gateway.py --demo
    python3 tools/run_gateway.py --mcap samples/queuing-retail-sample.mcap

A shim, and deliberately nothing more. The gateway lives in the distribution
under `lib/spatialdds/` as `spatialdds.server`, because `pip install
"spatialdds[server]"` has to stand one up with no clone; this file puts that
checkout on the path and hands over to `python -m spatialdds.serve`, so there
is one gateway and one set of options rather than two that drift.

Every flag is `spatialdds.serve`'s. Two defaults differ, and only because the
audience differs:

* **The recording.** Run from a checkout with no arguments, this seeds the
  retail recording in `samples/`, which is what every example and gate here
  has always meant by "the gateway". From a wheel there is no `samples/`, so
  there `--demo` or `--mcap` says what to serve and bare means empty.
* **The journal.** `spatialdds.serve --publish` journals by default, because a
  registry that forgets on restart is not a registry. This shim passes
  `--no-journal` unless you ask for one, because the publish lines in this
  repository's README and walkthrough predate the default and would otherwise
  drop an MCAP into the checkout every time somebody pasted one.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from spatialdds import serve                                   # noqa: E402

DEFAULT_MCAP = ROOT / "samples" / "queuing-retail-sample.mcap"
DEFAULT_MANIFESTS = ROOT / "samples" / "manifests"
DEFAULT_ASSETS = ROOT / "samples" / "assets"


def main() -> int:
    argv = sys.argv[1:]
    named = {a.split("=", 1)[0] for a in argv}
    if not ({"--demo", "--mcap", "--no-mcap"} & named):
        argv = ["--mcap", str(DEFAULT_MCAP)] + argv
    if "--no-mcap" in named:
        # The flag `spatialdds.serve` does not need: there, no recording is
        # the default. Kept here because this repository's gates and docs
        # name it, and dropping it would break a documented line.
        argv = [a for a in argv if a != "--no-mcap"]
    if not ({"--journal", "--no-journal"} & named):
        argv += ["--no-journal"]
    if "--manifests" not in named and DEFAULT_MANIFESTS.is_dir():
        argv += ["--manifests", str(DEFAULT_MANIFESTS)]
    if "--assets" not in named and DEFAULT_ASSETS.is_dir():
        argv += ["--assets", str(DEFAULT_ASSETS)]
    return serve.main(argv)


if __name__ == "__main__":
    sys.exit(main())
