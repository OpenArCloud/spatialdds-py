"""The metadata records a SpatialDDS recording carries, by name.

MCAP metadata is where a recording keeps what is about the data rather than in
it. 1.8 has no record for a dispose, and N.7 manifests are JSON documents with
no IDL type, so the gateway's journal, the recording loader and the native
layer's tap all write and read these names. One place, so they cannot drift.
Standard library only: the native layer reads it without the HTTP server.
"""
from __future__ import annotations

#: A dispose: `seq` (on the messages' log-time axis), `topic`, `key`,
#: `reason`, and the `principal` and `scene` that wrote it.
DISPOSE_RECORD = "spatialdds-web:dispose"
#: A published content manifest, journalled.
MANIFEST_RECORD = "spatialdds-web:manifest"
#: A manifest withdrawn over `DELETE /spatialdds/resolve` (finding 44).
MANIFEST_WITHDRAW_RECORD = "spatialdds-web:manifest-withdraw"
#: Who wrote a journalled sample, and into which scene, keyed by `seq`.
WRITTEN_BY_RECORD = "spatialdds-web:written-by"
