"""The native layer: SpatialDDS over DDS, beside the web client. Needs `[dds]`.

    pip install "spatialdds[dds]"

This package root imports nothing third-party. The parts that need cyclonedds
import it themselves and raise `MissingExtra` naming the extra when it is
absent; the parts that are data (the registry, topic names) need nothing.

`spatialdds.dds.types` holds the generated bindings for every type in the
pinned IDL, nested here so they cannot collide with a program's own vendored
`spatialdds_idl18` while it migrates.
"""
from __future__ import annotations

from ._extra import MissingExtra, require_cyclonedds

__all__ = ["MissingExtra", "require_cyclonedds"]
