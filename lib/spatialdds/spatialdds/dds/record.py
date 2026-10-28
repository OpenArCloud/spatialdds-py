"""The recording tap: what a participant wrote, as a typed SpatialDDS MCAP.

    with sd.participant("plaza", domain=7, service_id="svc:me",
                        authority="registry.example") as p:
        p.record("session.mcap")
        ...                                # every write, withdrawal, departure

The file is the shape every tool in this family reads:

* one `omgidl` schema per type, flattened from the pinned IDL by
  `spatialdds.server.pin`, the same function the gateway's journal and the
  fountain recording use, so a type carries the same bytes in all three;
* `cdr` messages, the serialized samples as they went on the wire;
* a withdrawal as the journal's own `spatialdds-web:dispose` metadata,
  carrying the reason.

1.8 has no record for a dispose, so this is where its reason lives for a type
with no member to carry one (ruling 3). `spatialdds-serve --mcap` serves the
file, disposes and all.

spatialdds_ros's `Recorder` had this shape, and its wheel could not find the
IDL it embeds (spatialdds-ros finding 1). This one reads the IDL from inside
this package.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Union

from ._extra import require_cyclonedds

require_cyclonedds()
try:
    from mcap.writer import Writer as _McapWriter
except ImportError as e:                                      # pragma: no cover
    from ._extra import EXTRA, MissingExtra
    raise MissingExtra(f"the recording tap needs mcap: {EXTRA}") from e

from ..server import mcapmeta, pin  # noqa: E402
from . import codec  # noqa: E402

__all__ = ["Recorder"]


class Recorder:
    """An MCAP file a participant's writes go into. See the module docstring."""

    def __init__(self, path: Union[str, Path], *, scene: str = "",
                 principal: str = "") -> None:
        self.path = Path(path)
        self._f = open(self.path, "wb")
        self._w = _McapWriter(self._f)
        self._w.start(profile="spatialdds",
                      library="spatialdds.dds.record")
        self._schemas: Dict[str, int] = {}
        self._channels: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._last = 0
        self._attr = {"principal": principal, "scene": scene}
        self.counts = {"samples": 0, "disposes": 0}
        self._closed = False

    def _now(self) -> int:
        # Strictly increasing, so a dispose recorded in the same nanosecond as
        # its terminal sample still sorts after it.
        t = max(time.time_ns(), self._last + 1)
        self._last = t
        return t

    def sample(self, topic: str, fqn: str, sample: Any,
               durability: str = "") -> None:
        with self._lock:
            if self._closed:
                return
            if fqn not in self._schemas:
                ext = codec.extension_of(fqn)
                name, data = ((fqn.replace(".", "::"), ext.record) if ext
                              else pin.schema_record(fqn.replace(".", "::")))
                self._schemas[fqn] = self._w.register_schema(
                    name=name, encoding="omgidl", data=data)
            if topic not in self._channels:
                self._channels[topic] = self._w.register_channel(
                    topic=topic, message_encoding="cdr",
                    schema_id=self._schemas[fqn],
                    metadata=({mcapmeta.CHANNEL_DURABILITY: durability}
                              if durability else {}))
            t = self._now()
            self._w.add_message(channel_id=self._channels[topic], log_time=t,
                                publish_time=t, data=sample.serialize(),
                                sequence=self.counts["samples"])
            self.counts["samples"] += 1

    def dispose(self, topic: str, key: str, reason: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._w.add_metadata(name=mcapmeta.DISPOSE_RECORD, data={
                "seq": str(self._now()), "topic": topic, "key": key,
                "reason": reason, **self._attr})
            self.counts["disposes"] += 1

    def flush(self) -> Dict[str, int]:
        """Finish the file. Idempotent; returns what was recorded."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._w.finish()
                self._f.close()
        return dict(self.counts)

    close = flush
