"""SpatialDDS over plain HTTPS and a WebSocket, for programs.

`fetch` and a WebSocket really are the whole client -- the endpoint's README
shows that in three lines of `curl` -- and this package exists for the step
after that, when a program wants native values, verified bytes, and refusals it
can act on.

    import spatialdds as sd

    ep = sd.connect("https://demo.spatialdds.org")
    svc = next(h for h in ep.search(geohash="9v6krc")
               if h.serves("geo_anchor"))
    for a in svc.topic("geo_anchor").latched():
        print(a["anchor_id"], a["geopose"]["stamp"]["sec"])   # an int, not "17…"

What it implements: Appendix N as of SpatialDDS-spec `9c3e1f2`. Package
versions move independently of specification versions; `SPEC_COMMIT` is the
one to check.
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "connect", "Endpoint", "Hit", "Topic", "Resolved", "Written",
    "Sample", "Dispose", "EndOfReplay", "Liveliness",
    "SpatialDDSError", "Unreachable", "Problem", "Unauthorized", "SurfaceOff",
    "IntegrityError", "UnverifiableIntegrity",
    "lift", "SPEC_COMMIT", "APPENDIX_N", "__version__",
    # The record builders, re-exported so that `sd.circle(...)` reads the way
    # it does in the documentation. They live in `spatialdds.records`.
    "records", "circle", "bbox", "earth_fixed", "frame", "stamp", "geopose",
    "cov_pos3", "cov_none", "topic", "asset", "asset_ref", "cells",
    "profile", "capabilities",
    "service_announce", "service_manifest", "content_announce",
    "content_manifest", "geo_anchor", "service_uri", "content_uri",
]

__version__ = "0.3.0"
#: The SpatialDDS-spec commit whose Appendix N and generated artifacts this
#: package implements. Package versions and specification versions move
#: independently; this is the one that says what the wire looks like.
SPEC_COMMIT = "9c3e1f2"
APPENDIX_N = f"Appendix N as of spec {SPEC_COMMIT}"

# Everything else lives in `client` and is fetched on first access (PEP 562).
# This module stays free of third-party imports on purpose: `import
# spatialdds.server` executes it, and an eager `import requests` here would
# hand the server the client's dependencies for nothing. The two halves share
# the vendored `artifacts/` as data and no code at all.
# `gates/import_graph.py` holds both directions, each shown failable.


#: The package's own modules. Named here because `from spatialdds import
#: server` (or `serve`, or `canonical`) asks this module for the attribute
#: before the import machinery tries the submodule -- and answering that
#: question by importing the client would hand the server `requests` through
#: the back door, which is exactly the coupling this file exists to avoid.
_SUBMODULES = frozenset({"client", "canonical", "errors", "records",
                         "serve", "server"})


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _SUBMODULES:
        return importlib.import_module("." + name, __name__)
    # `import_module` and not `from . import client`: the latter asks this
    # module for the attribute first, which calls straight back into here.
    client = importlib.import_module(".client", __name__)
    if name == "client":
        return client
    try:
        return getattr(client, name)
    except AttributeError:
        pass
    # The builders, re-exported. Second rather than first because the API is
    # what people reach for; `spatialdds.records` is always there under its
    # own name for anyone who prefers it spelled out.
    recs = importlib.import_module(".records", __name__)
    try:
        return getattr(recs, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
