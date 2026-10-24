"""The four things a stream says, for the web client and the native layer alike.

    for ev in topic.subscribe():                   # over a WebSocket
    for ev in p.reader("model_entity").events():   # over DDS
        match ev:
            case sd.Sample(key=k, data=d): ...
            case sd.Dispose(key=k, reason=r): ...      # somebody chose it
            case sd.Liveliness(alive=False): ...       # a writer went quiet
            case sd.EndOfReplay(): ...                 # caught up

N.5's vocabulary, and §2.14's distinction in it: `Dispose` is a deliberate
removal and nothing else, and loss of liveliness is `Liveliness`, never a
`Dispose`. Standard library only, so both halves share it as they share
`canonjson`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Sample", "Dispose", "EndOfReplay", "Liveliness"]


@dataclass
class Sample:
    """One instance.

    From the web client, `data` carries wire values lifted from N.1 (an int
    where N.1 wrote a decimal string). From a native reader, `data` is the
    typed sample itself; `spatialdds.dds.to_json(data)` is its N.1 form.
    """
    topic: str
    key: str
    data: Any


@dataclass
class Dispose:
    """A deliberate removal, with the reason its writer gave."""
    topic: str
    key: str
    reason: str = ""


@dataclass
class EndOfReplay:
    """The latched set is complete. Before it you are catching up."""
    topic: str
    replayed: int = 0


@dataclass
class Liveliness:
    """A writer's presence changed. Not a removal: the record stands."""
    topic: str
    key: str
    alive: bool = False
