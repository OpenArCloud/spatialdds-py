"""The native layer: SpatialDDS over DDS, beside the web client. Needs `[dds]`.

    pip install "spatialdds[dds]"

    import spatialdds as sd
    with sd.participant("plaza", domain=7, service_id="svc:me",
                        authority="registry.example") as p:
        ...

This package root imports nothing third-party, and neither do the parts that
are data (`registry`, `topic_name`). The parts that need cyclonedds
(`participant`, `to_json` on a sample, the generated `types`) import it
themselves, and when it is absent they raise `MissingExtra` naming the extra.

`spatialdds.dds.types` holds the generated bindings for every type in the
pinned IDL, nested here so they cannot collide with a program's own vendored
`spatialdds_idl18` while it migrates.
"""
from __future__ import annotations

import importlib
from typing import Any

from ._extra import MissingExtra, require_cyclonedds

__all__ = [
    "MissingExtra", "require_cyclonedds",
    "participant", "Participant", "Writer", "Reader", "ParticipantRefused",
    "AnnounceRelay", "RELAYED_BY",
    "ON_EXIT", "DOMAIN_ENV",
    "to_json", "from_json", "validate", "SchemaInvalid", "fqn_of", "key_of",
    "class_for", "extend", "Extension",
    "registry", "Profile", "RegistryError", "topic_name",
    "ANNOUNCE", "DEPART", "CONTENT", "QUERY", "COVERAGE_HINT",
    "Recorder", "chunk", "Reassembler", "CorruptChunk", "blob_ref",
    "MAX_CHUNK_BYTES",
]

#: The well-known discovery topics, by the message type a reader asks for.
ANNOUNCE, DEPART, CONTENT = "Announce", "Depart", "ContentAnnounce"
QUERY, COVERAGE_HINT = "CoverageQuery", "CoverageHint"

_FROM = {
    # `_participant`, not `participant`: a submodule of that name would be
    # bound on this package the moment it was imported, and `sd.participant`
    # would then be a module rather than the function.
    "participant": "_participant", "Participant": "_participant",
    "Writer": "_participant", "Reader": "_participant",
    "ParticipantRefused": "_participant", "ON_EXIT": "_participant",
    "AnnounceRelay": "_participant", "RELAYED_BY": "_participant",
    "DOMAIN_ENV": "_participant",
    "to_json": "codec", "from_json": "codec", "validate": "codec",
    "SchemaInvalid": "codec", "fqn_of": "codec", "key_of": "codec",
    "class_for": "codec", "extend": "codec", "Extension": "codec",
    "Profile": "registry", "RegistryError": "registry",
    "topic_name": "registry",
    "Recorder": "record",
    "chunk": "blob", "Reassembler": "blob", "CorruptChunk": "blob",
    "blob_ref": "blob", "MAX_CHUNK_BYTES": "blob",
}
_SUBMODULES = frozenset({"registry", "codec", "qos", "types", "record",
                         "blob"})


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module("." + name, __name__)
    mod = _FROM.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # No conversion here. Every module that touches cyclonedds calls
    # `require_cyclonedds()` as it is imported, the generated `types` root
    # included, so the refusal has one enforcement point. A second one here
    # once masked a planted regression of the first.
    return getattr(importlib.import_module("." + mod, __name__), name)


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
