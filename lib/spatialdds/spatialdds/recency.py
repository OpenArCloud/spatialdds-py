"""Learned recency: when a key's updates have gone quiet, judged by its own cadence.

    import spatialdds as sd
    r = sd.Recency()
    due = r.observed("ent:duck:1")          # True every Nth update: relatch now
    for key in r.idle():                    # quiet, relative to its own rhythm
        ...

Pure bookkeeping: no I/O, no clock of its own beyond `time.time()` when none
is passed, and it publishes nothing. The caller decides what to write. That
keeps the single-writer rule with whoever owns the instance, and it keeps this
testable without a bus.

**"Idle" is only meaningful relative to an entity's own cadence.** A fixed
threshold shorter than the update interval makes every gap look like a stop.
Measured in SpatialDDS-demo: a one-second threshold against a mover giving each
duck a turn every 1.5 s produced 94 "it stopped moving" flushes while nothing
had stopped. So each key's interval is learned (an exponentially smoothed gap),
and idle means `max(idle_floor, idle_factor × gap)` without an update.

**The latch converges to the stream on idle.** A fast lane may lag the truth
while things move. It may not leave a wrong answer lying around once they have
stopped, because a reader arriving afterwards has nothing coming to correct it.
`observed` says when the latched record is due (every `latch_every` updates),
and `idle` says which keys stopped with changes pending. Each is returned once.

This is SpatialDDS-demo's `tempo.Tempo`, which spatialdds_ros copied, made the
one copy (docs/NATIVE-SDK-CENSUS.md, subject 5). Opt-in: nothing in the native
layer uses it unless a program does.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

__all__ = ["Recency", "LATCH_EVERY_N", "IDLE_FLOOR_S", "IDLE_FACTOR",
           "GAP_SMOOTHING"]

#: A key's latched record is due every Nth update, so a late joiner is at most
#: N-1 updates stale and converges on the next one.
LATCH_EVERY_N = 5
#: The floor under the idle threshold, and the multiple of a key's own observed
#: interval taken as evidence that it stopped.
IDLE_FLOOR_S = 1.0
IDLE_FACTOR = 3.0
#: How much of the observed interval survives each new measurement: low enough
#: to follow a cadence change within a few updates, high enough that one late
#: sample does not convince it everything stopped.
GAP_SMOOTHING = 0.7


class Recency:
    """Per-key cadence bookkeeping. No I/O."""

    def __init__(self, latch_every: int = LATCH_EVERY_N,
                 idle_floor: float = IDLE_FLOOR_S,
                 idle_factor: float = IDLE_FACTOR,
                 smoothing: float = GAP_SMOOTHING) -> None:
        if latch_every < 1 or idle_floor <= 0 or idle_factor <= 0 \
                or not 0.0 <= smoothing < 1.0:
            raise ValueError(
                "Recency needs latch_every >= 1, idle_floor > 0, "
                "idle_factor > 0 and 0 <= smoothing < 1")
        self.latch_every = latch_every
        self.idle_floor = idle_floor
        self.idle_factor = idle_factor
        self.smoothing = smoothing
        self._pending: Dict[str, int] = {}
        self._last: Dict[str, float] = {}
        self._gap: Dict[str, float] = {}

    def observed(self, key: str, now: Optional[float] = None) -> bool:
        """Record an update. True when the latched record is now due."""
        now = time.time() if now is None else now
        previous = self._last.get(key)
        if previous is not None:
            gap = now - previous
            known = self._gap.get(key)
            self._gap[key] = (gap if known is None else
                              self.smoothing * known + (1 - self.smoothing) * gap)
        self._last[key] = now
        pending = self._pending.get(key, 0) + 1
        if pending >= self.latch_every:
            self._pending[key] = 0
            return True
        self._pending[key] = pending
        return False

    def threshold(self, key: str) -> float:
        """How long without an update means this key has stopped.

        One update is not a cadence: until there are two, the floor is all
        there is to go on.
        """
        gap = self._gap.get(key)
        if gap is None:
            return self.idle_floor
        return max(self.idle_floor, self.idle_factor * gap)

    def idle(self, now: Optional[float] = None) -> List[str]:
        """Keys with pending changes that have gone quiet. Each is returned
        once and marked clean, so calling this every tick republishes a still
        world exactly once."""
        now = time.time() if now is None else now
        due = []
        for key, pending in list(self._pending.items()):
            if pending == 0:
                continue
            if now - self._last.get(key, 0.0) < self.threshold(key):
                continue
            self._pending[key] = 0
            due.append(key)
        return due

    def forget(self, key: str) -> None:
        """Stop tracking a key: it retired, or was disposed."""
        self._pending.pop(key, None)
        self._last.pop(key, None)
        self._gap.pop(key, None)
