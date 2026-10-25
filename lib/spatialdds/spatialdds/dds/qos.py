"""A registry Profile, as the cyclonedds QoS a writer or reader sets.

§3.3.3 is normative for four columns: "writers and readers SHALL set exactly
these policies", and a Deadline of "—" SHALL be left at its default. Those come
from the `Profile`, which comes from the spec text. Two more policies are set
here, and §3.3.3 says other policies "MAY be tuned per deployment".

* **Writers never autodispose.** DDS defaults
  `autodispose_unregistered_instances` to TRUE. A writer that is deleted, or
  whose participant's lease expires, then disposes every instance it wrote.
  So a crash reaches readers as `NOT_ALIVE_DISPOSED`, a deliberate removal,
  about ten seconds later. §2.14 says liveliness loss is not removal, and the
  middleware would be manufacturing one from the other. Measured against
  SpatialDDS-demo's writers and against spatialdds_ros's
  (docs/NATIVE-SDK-CENSUS.md). It is off on every writer and not a parameter:
  a removal in this layer is `withdraw()`, which is a choice somebody made.
  It is a writer-local policy, so no reader's matching changes.
* **Lifespan**, where a payload carries a TTL (`Announce.ttl_sec`): the bus
  drops a stale sample instead of every consumer re-deriving staleness.

Reliable writers block for at most a second when a reader's history is full,
as every implementation the native layer replaces did.
"""
from __future__ import annotations

from typing import Optional

from ._extra import require_cyclonedds
from .registry import Profile

require_cyclonedds()
from cyclonedds import qos as _qos, util as _util  # noqa: E402

__all__ = ["writer_qos", "reader_qos"]


def _common(p: Profile, lifespan_sec: Optional[float]) -> list:
    pol = [
        _qos.Policy.Reliability.Reliable(_util.duration(seconds=1))
        if p.reliability == "RELIABLE" else _qos.Policy.Reliability.BestEffort,
        _qos.Policy.Durability.TransientLocal if p.latched
        else _qos.Policy.Durability.Volatile,
        _qos.Policy.History.KeepLast(p.depth) if p.history == "KEEP_LAST"
        else _qos.Policy.History.KeepAll,
    ]
    if p.deadline_ms is not None:
        pol.append(_qos.Policy.Deadline(_util.duration(milliseconds=p.deadline_ms)))
    if lifespan_sec:
        pol.append(_qos.Policy.Lifespan(_util.duration(seconds=lifespan_sec)))
    return pol


def writer_qos(p: Profile, lifespan_sec: Optional[float] = None) -> "_qos.Qos":
    """The profile's four columns, Lifespan if given, and no autodispose."""
    return _qos.Qos(*_common(p, lifespan_sec),
                    _qos.Policy.WriterDataLifecycle(autodispose=False))


def reader_qos(p: Profile, ignore_local: bool = False) -> "_qos.Qos":
    """The profile's four columns, exactly, so a conformant writer matches.

    `ignore_local` drops samples from writers in the same participant, which
    is how a bridge that writes and reads one lane avoids hearing itself.
    """
    pol = _common(p, None)
    if ignore_local:
        pol.append(_qos.Policy.IgnoreLocal.Participant)
    return _qos.Qos(*pol)
