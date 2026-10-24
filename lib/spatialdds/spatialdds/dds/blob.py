"""Heavy bytes by reference: `BlobRef` in the message, `BlobChunk`s on a lane.

    from spatialdds.dds import blob
    ref = blob.blob_ref("img-7", "query_image", data)   # goes in the request
    for c in blob.chunk("img-7", data):                 # goes on the blob lane
        lane.write(c)
    whole = blob.Reassembler().feed(c)                  # bytes once complete

The spec is explicit that heavy content is never inlined in a message: a
message carries a `BlobRef` (id, role, checksum), and the bytes travel as
`spatial::core::BlobChunk` samples. Consumers "MUST be prepared for partial
delivery" (§2). This is SpatialDDS-demo's chunker and reassembler, which
openvps vendors, with the check openvps lacks: the whole blob's SHA-256
`BlobRef.checksum` is verified on reassembly, not only each chunk's CRC32.

**No lane is named here.** 1.8 defines the type and the rule, and names no
topic, registry row or QoS profile for the lane that carries chunks. The demo
used `spatialdds/blob/chunk/v1` on GEOM_TILE, which is a choice, not the spec.
A program writes chunks on an extension lane of its own naming
(`p.writer(BlobChunk, type_name=…, qos_profile=Profile(...))`), and the gap is
on the spec queue.
"""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from ._extra import require_cyclonedds

require_cyclonedds()
from .types.spatial.core import BlobChunk  # noqa: E402

__all__ = ["chunk", "checksum", "blob_ref", "Reassembler", "CorruptChunk",
           "MAX_CHUNK_BYTES"]

#: cyclonedds-python's sequence ceiling, and 1.8's `BlobChunk.data` bound
#: (`sequence<uint8, 65535>`).
MAX_CHUNK_BYTES = 65535


class CorruptChunk(ValueError):
    """A chunk whose CRC32, or a blob whose SHA-256, does not match."""


def checksum(data: bytes) -> str:
    """The `BlobRef.checksum` for a whole blob: `sha256:<hex>`."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def blob_ref(blob_id: str, role: str, data: bytes) -> Dict[str, str]:
    """A complete `spatial::core::BlobRef` for `data`, as N.1."""
    return {"blob_id": str(blob_id), "role": str(role),
            "checksum": checksum(data)}


def chunk(blob_id: str, data: bytes,
          max_bytes: int = MAX_CHUNK_BYTES) -> Iterator[BlobChunk]:
    """`data` as `BlobChunk` samples, each carrying its own CRC32."""
    if not 0 < max_bytes <= MAX_CHUNK_BYTES:
        raise ValueError(f"max_bytes {max_bytes} must be 1..{MAX_CHUNK_BYTES}")
    pieces = [data[i:i + max_bytes]
              for i in range(0, len(data), max_bytes)] or [b""]
    for index, piece in enumerate(pieces):
        yield BlobChunk(blob_id=str(blob_id), index=index,
                        total_chunks=len(pieces),
                        crc32=zlib.crc32(piece) & 0xFFFFFFFF, data=list(piece))


@dataclass
class _Partial:
    total: int
    pieces: Dict[int, bytes] = field(default_factory=dict)


class Reassembler:
    """Collects chunks until a blob is whole, and checks it twice.

    `feed` returns the bytes once the last missing chunk arrives, and None
    until then. A chunk whose CRC32 does not match raises `CorruptChunk`.
    `expect(blob_id, checksum)` names the whole blob's SHA-256, from the
    `BlobRef` that referenced it, and a completed blob that does not match
    raises too. Without an expectation, a blob is checked chunk by chunk only,
    and `unverified` says which ones completed that way.

    At most `max_blobs` are held incomplete. The least recently started is
    dropped when a sender goes away mid-blob, so memory is bounded.
    """

    def __init__(self, max_blobs: int = 32) -> None:
        self._partial: Dict[str, _Partial] = {}
        self._expect: Dict[str, str] = {}
        self._max_blobs = max_blobs
        self.unverified: List[str] = []

    def expect(self, blob_id: str, checksum_: str) -> None:
        if not checksum_.startswith("sha256:"):
            raise ValueError(f"{checksum_!r} is not a sha256: checksum")
        self._expect[blob_id] = checksum_

    def feed(self, sample: BlobChunk) -> Optional[bytes]:
        data = bytes(sample.data)
        if (zlib.crc32(data) & 0xFFFFFFFF) != sample.crc32:
            raise CorruptChunk(f"{sample.blob_id}[{sample.index}]: CRC32 "
                               f"mismatch")
        partial = self._partial.get(sample.blob_id)
        if partial is None:
            if len(self._partial) >= self._max_blobs:
                self._partial.pop(next(iter(self._partial)))
            partial = self._partial[sample.blob_id] = _Partial(
                int(sample.total_chunks))
        partial.pieces[int(sample.index)] = data
        if len(partial.pieces) < partial.total:
            return None
        del self._partial[sample.blob_id]
        whole = b"".join(partial.pieces[i] for i in range(partial.total))
        want = self._expect.pop(sample.blob_id, None)
        if want is None:
            self.unverified.append(sample.blob_id)
        elif checksum(whole) != want:
            raise CorruptChunk(f"{sample.blob_id}: every chunk's CRC32 "
                               f"matched and the whole blob is not {want}")
        return whole

    @property
    def pending(self) -> List[str]:
        return sorted(self._partial)
