"""The gateway's own DDS writer: what the world lanes accept goes out on a bus.

0.8.0's write surface lets an HTTP client put entities and claims into a
world. A world on a bus is read by DDS participants as well as by browsers,
so a gateway joined to a domain writes what it accepted there too, as
ordinary samples from one participant: the gateway's. That participant's
liveliness is the gateway's, which is the honest liveliness for samples the
gateway owns; an HTTP client's own freshness is the refresh rule
(`publish.lease_for`), and when it lapses the gateway withdraws on the bus
exactly as it does over HTTP, through the SDK's `Writer.withdraw()`.

**World lanes only.** The entity lane and a deployment's keyed claim types
(`publish.is_world`). The record lanes (announce, content_announce,
geo_anchor) stay HTTP and journal, byte-identical in behaviour to 0.7.x.

**What it can write.** A specification type through the registry row that
names it (`spatial.owm.Entity` is `model_entity`); a deployment's own type
only if this process registered it with `sd.dds.extend()`, because DDS
writes a generated class and the gateway's wire model is not one. A world
lane it cannot write is refused before anything is journalled, so the bus
and the endpoint never disagree about what was accepted.

**QoS.** The 1.8 owm rows name no profile, so the gateway declares one:
RELIABLE, TRANSIENT_LOCAL, KEEP_LAST 1, the shape a latched model lane has
(the demo's MODEL_LATCHED is the same four columns). `on_exit="keep"`: a
gateway that stops leaves its instances to the journal it replays from, and
republishes them when it starts (`prime`).

Needs `spatialdds[dds]`.
"""
from __future__ import annotations

import random
from typing import Any

#: The participant's scene. Every gateway writer names its whole topic
#: (`topic=`), so this names only the participant's own announce.
SCENE = "gateway"
SERVICE_ID = "svc:spatialdds:gateway"


class NotOnBus(Exception):
    """A world lane this gateway cannot write to its bus."""


class Bus:
    """One participant, one writer per topic, created on first use."""

    def __init__(self, domain: int, service_id: str = SERVICE_ID) -> None:
        import spatialdds as sd
        from spatialdds.dds import codec, registry
        self._sd, self._codec, self._registry = sd, codec, registry
        self.domain = domain
        self.profile = sd.dds.Profile(
            "GATEWAY_LATCHED", reliability="RELIABLE",
            durability="TRANSIENT_LOCAL", history="KEEP_LAST", depth=1)
        self._p = sd.participant(SCENE, domain=domain, service_id=service_id)
        self._writers: dict[str, Any] = {}

    def _type_for(self, fqn: str) -> Any:
        ext = self._codec.extension_of(fqn)
        if ext is not None:
            return ext.cls
        for name, reg in self._registry.registrations().items():
            if getattr(reg, "fqn", None) == fqn:
                return name
        raise NotOnBus(
            f"{fqn} is not a type this process can write to DDS: neither a "
            f"registered specification type nor one registered here with "
            f"sd.dds.extend(). Register it in the gateway's process "
            f"(serve --extension MODULE), or run without --dds-domain.")

    def writer(self, topic: str, fqn: str) -> Any:
        """The writer for a topic, opened on first use. Raises NotOnBus."""
        w = self._writers.get(topic)
        if w is None:
            try:
                w = self._p.writer(self._type_for(fqn), topic=topic,
                                   qos_profile=self.profile, on_exit="keep")
            except NotOnBus:
                raise
            except Exception as e:
                raise NotOnBus(f"{topic}: {type(e).__name__}: {e}") from e
            self._writers[topic] = w
        return w

    def write(self, topic: str, fqn: str, data: dict) -> None:
        self.writer(topic, fqn).write(data)

    def withdraw(self, topic: str, fqn: str, key: str, reason: str) -> None:
        """The SDK's withdrawal: the terminal sample from the last one, then
        the dispose. Nothing to do if this writer never held the key."""
        w = self.writer(topic, fqn)
        try:
            w.withdraw(key, reason=reason)
        except KeyError:
            pass

    def prime(self, st: Any, is_world: Any) -> int:
        """Republish every world instance the gateway holds. Returns how many.

        On start, after the journal replayed: the bus then holds what the
        endpoint serves, and a lease that lapses after a restart has a sample
        on the bus to withdraw.
        """
        n = 0
        for res in st.resources.values():
            if not is_world(st, res.fqn):
                continue
            for key, inst in sorted(st.latched.get(res.topic, {}).items()):
                self.write(res.topic, res.fqn, inst.data)
                n += 1
        return n

    def close(self) -> None:
        self._p.close()


def free_domain() -> int:
    """A domain id for a gate or a test that wants a bus nobody else is on."""
    return random.randint(150, 229)
