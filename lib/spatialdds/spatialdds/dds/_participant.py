"""A native SpatialDDS participant: conventions as code, and still a plain program.

    import spatialdds as sd

    with sd.participant("ut-austin-littlefield", domain=7,
                        service_id="svc:pondwatch",
                        authority="demo.spatialdds.org") as p:
        pond = p.writer("model_entity", qos_profile="MAP_META", on_exit="keep")
        p.announce("OTHER", coverage=sd.circle(30.2849, -97.7341, 60))
        for _ in p.loop(hz=0.2):
            pond.write(observation())

What each constructor requires, what it defaults, and what silence means, is
the API (docs/NATIVE-SDK-CENSUS.md, "The proposed API surface", as ruled). The
short form:

* **Refused when absent:** `scene`; `domain` (or `SPATIALDDS_DDS_DOMAIN`);
  `service_id` to write or announce; `authority` (or `manifest_uri`) to
  announce; `qos_profile` where §3.3.2 names none; `stream` where §3.3.2 gives
  no template; `on_exit` on every latched keyed writer. Every refusal from one
  constructor is raised together.
* **Defaulted, documented:** the §3.3.2 row's profile and template; the
  announce refreshed every `ttl_sec / 3`; schema validation on.
* **Never a parameter:** autodispose. It is off on every writer. A removal is
  `withdraw()`: a terminal sample, then the dispose, with a reason.

**Crash is never a dispose.** A participant that dies leaves its instances,
and a reader sees `Liveliness(alive=False)` once its lease expires, never
`Dispose`. A clean stop departs the announce: its terminal sample, its dispose,
then `Depart`. Each latched writer does what its `on_exit` said. Then every
writer waits for its acknowledgements, so a clean stop is flushed rather than
hoped for.

**No hidden threads.** `tick()` refreshes the announce and runs the floors.
`loop(hz)` is a generator that ticks and sleeps, and installs SIGINT and
SIGTERM handlers only while it iterates. Nothing runs that the program did not
call.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import signal
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type, Union

from ._extra import require_cyclonedds

require_cyclonedds()
from cyclonedds.core import InstanceState  # noqa: E402
from cyclonedds.domain import DomainParticipant  # noqa: E402
from cyclonedds.pub import DataWriter  # noqa: E402
from cyclonedds.sub import DataReader  # noqa: E402
from cyclonedds.topic import Topic  # noqa: E402
from cyclonedds.util import duration  # noqa: E402

from .. import records  # noqa: E402
from ..events import Dispose, EndOfReplay, Liveliness, Sample  # noqa: E402
from . import codec, registry  # noqa: E402
from .qos import reader_qos, writer_qos  # noqa: E402
from .registry import Profile, RegistryError  # noqa: E402

__all__ = ["Participant", "Writer", "Reader", "ParticipantRefused",
           "participant", "ON_EXIT", "DOMAIN_ENV"]

log = logging.getLogger("spatialdds.dds")

#: The environment variable a domain may come from, as every SpatialDDS
#: deployment in this family already spells it.
DOMAIN_ENV = "SPATIALDDS_DDS_DOMAIN"
#: What a latched writer's instances do when it stops cleanly. Required on
#: every latched keyed writer, because both answers are claims only the
#: program can make: a robot still exists after its bridge stops; a
#: withdrawn catalogue item does not.
ON_EXIT = ("keep", "withdraw")
#: Message types whose well-known writers the participant owns: it announces
#: and departs on a program's behalf, so a second writer would be a second
#: claim about the same presence.
_OWNED = ("Announce", "Depart")


class ParticipantRefused(ValueError):
    """Configuration the native layer will not start with, every reason at once."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(problems))


def _types_class(fqn: str) -> type:
    """The generated class for a dotted type identity (`codec.class_for`)."""
    return codec.class_for(fqn)


def _wait(cond, timeout: float, poll: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while not cond():
        if time.time() >= deadline:
            return False
        time.sleep(poll)
    return True


def _now_time():
    from .types.builtin import Time
    t = time.time()
    return Time(sec=int(t), nanosec=int((t % 1) * 1e9))


def _has_member(sample: Any, name: str) -> bool:
    return any(f.name == name for f in dataclasses.fields(sample))


class _Lane:
    """What a writer or reader resolved its type, topic and QoS to."""

    def __init__(self, scene: str, type_: Union[str, type], *,
                 type_name: Optional[str], stream: Optional[str],
                 qos_profile: Union[str, Profile, None], version: str,
                 problems: List[str], topic: Optional[str] = None) -> None:
        self.well_known: Optional[registry.WellKnown] = None
        self.type_name, self.cls, self.fqn = "", None, ""
        self.topic, self.profile = "", None
        #: True when the caller named the topic (`topic=`), rather than the
        #: registry building it from the participant's scene.
        self.explicit = topic is not None
        if topic is not None and stream is not None:
            problems.append(f"topic= names the whole topic ({topic}); "
                            f"stream= would name a second one")
            return
        try:
            if isinstance(type_, type):
                # A deployment extension: its own generated class, a name per
                # §3.3.2's pattern, and an explicit Profile.
                self.cls, self.fqn = type_, codec.fqn_of(type_)
                if not type_name:
                    problems.append(f"{type_.__name__} is an extension type: "
                                    f"pass type_name= (e.g. 'myorg.depth_frame')")
                    return
                if type_name in registry.registrations():
                    problems.append(f"type_name {type_name!r} is registered "
                                    f"(§3.3.2); pass the name, not a class")
                    return
                if not isinstance(qos_profile, Profile):
                    problems.append(f"{type_name}: an extension type needs a "
                                    f"Profile(...) object for qos_profile=")
                    return
                self.type_name, self.profile = type_name, qos_profile
                self.topic = (registry.check_topic(topic, type_name)
                              if topic is not None else
                              registry.topic_name(scene, stream, type_name,
                                                  version))
            elif type_ in registry.well_known():
                wk = registry.well_known(type_)
                if topic is not None:
                    problems.append(f"{type_} is a well-known topic "
                                    f"({wk.topic}): its name is the spec's, so "
                                    f"topic= is not accepted")
                    return
                if stream is not None or qos_profile is not None:
                    problems.append(f"{type_} is a well-known topic "
                                    f"({wk.topic}): its name and QoS are the "
                                    f"spec's, so stream= and qos_profile= are "
                                    f"not accepted")
                    return
                self.well_known, self.type_name = wk, type_
                self.fqn, self.topic, self.profile = wk.fqn, wk.topic, wk.profile
                self.cls = _types_class(wk.fqn)
            else:
                reg = registry.registration(type_)
                self.type_name, self.fqn = type_, reg.fqn
                self.profile = registry.resolve_profile(reg, qos_profile)
                self.topic = (registry.check_topic(topic, type_)
                              if topic is not None else
                              registry.topic_name(scene, stream, type_,
                                                  version))
                self.cls = _types_class(reg.fqn)
        except RegistryError as e:
            problems.append(str(e))
        except TypeError as e:
            problems.append(str(e))


class Writer:
    """One lane's writer. Created by `Participant.writer`, never directly."""

    def __init__(self, p: "Participant", lane: _Lane, on_exit: Optional[str],
                 floor_hz: Optional[float], validate: bool,
                 lifespan_sec: Optional[float]) -> None:
        self._p, self._lane = p, lane
        self.on_exit, self.floor_hz, self.validate = on_exit, floor_hz, validate
        self._dw = DataWriter(p._dp, p._topic(lane),
                              qos=writer_qos(lane.profile, lifespan_sec))
        self._last: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._next_floor = 0.0
        self.keyed = codec.key_member(lane.fqn) is not None

    # -- what this lane is -------------------------------------------------
    @property
    def topic(self) -> str:
        return self._lane.topic

    @property
    def type_name(self) -> str:
        return self._lane.type_name

    @property
    def profile(self) -> Profile:
        return self._lane.profile

    @property
    def cls(self) -> type:
        return self._lane.cls

    def matched(self) -> int:
        """How many readers this writer is matched with, now."""
        return self._dw.get_publication_matched_status().current_count

    def wait_matched(self, n: int = 1, timeout: float = 5.0) -> bool:
        """Wait until at least `n` readers have matched. False on timeout.

        For a command that is pointless unless somebody hears it. Matching
        is not delivery: a reliable writer's `close()` still awaits
        acknowledgements for that.
        """
        return _wait(lambda: self.matched() >= n, timeout)

    def keys(self) -> List[str]:
        """The instances this writer holds: written, and not withdrawn."""
        with self._lock:
            return list(self._last)

    # -- writing -----------------------------------------------------------
    def _coerce(self, sample: Any) -> Any:
        if isinstance(sample, dict):
            sample = codec.from_json(self.cls, sample)
        if not isinstance(sample, self.cls):
            raise TypeError(f"{self.topic} carries {self.cls.__name__}, not "
                            f"{type(sample).__name__}")
        return sample

    def write(self, sample: Any) -> Any:
        """Write a typed sample, or its N.1 document. Returns the typed sample.

        Validated against the published schema first unless the writer was
        made with `validate=False`; an invalid sample raises `SchemaInvalid`
        and nothing reaches the bus.
        """
        sample = self._coerce(sample)
        if self.validate:
            codec.validate(sample)
        key = codec.key_of(sample)
        with self._lock:
            self._dw.write(sample)
            self._last[key] = sample
        self._p._recorded(self.topic, self._lane.fqn, sample)
        return sample

    def _terminal(self, sample: Any, reason: str) -> Any:
        """The instance's terminal state: its last state, plus a reason where
        the type has a member to carry one.

        The members are found, not listed: a `state` that can be RETIRED, a
        `state_reason`, and a top-level `stamp`, which records when it was
        retired. A type without them is rewritten as it last was. Its reason
        then travels in the recording's metadata and the log, not on the wire.
        """
        changes: Dict[str, Any] = {}
        if _has_member(sample, "state"):
            enum = type(getattr(sample, "state"))
            if hasattr(enum, "RETIRED"):
                changes["state"] = enum.RETIRED
        if _has_member(sample, "state_reason"):
            changes["state_reason"] = reason
        if changes and _has_member(sample, "stamp"):
            changes["stamp"] = _now_time()
        return dataclasses.replace(sample, **changes) if changes else sample

    def withdraw(self, key: str, *, reason: str) -> None:
        """Remove an instance: its terminal sample, then the dispose (§2.14, N.5.2).

        `reason` is required and may not be empty. It is carried wherever a
        carrier exists: the type's own member when it has one, the recording's
        dispose metadata always, and this layer's log always.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("withdraw() needs a non-empty reason=: a removal "
                             "is a claim, and §2.14 makes it say why")
        with self._lock:
            if key not in self._last:
                raise KeyError(f"{self.topic} holds no instance keyed {key!r} "
                               f"from this writer (held: "
                               f"{', '.join(sorted(self._last)) or 'none'})")
            terminal = self._terminal(self._last.pop(key), reason)
            # Immediately: nothing of this writer's may come between the
            # terminal sample and its dispose (N.5.2).
            self._dw.write(terminal)
            self._dw.dispose(terminal)
        self._p._recorded(self.topic, self._lane.fqn, terminal)
        self._p._recorded_dispose(self.topic, key, reason)
        log.info("withdrew %s from %s: %s", key, self.topic, reason)

    def _floor(self, now: float) -> int:
        """Republish every held instance with a fresh stamp, if the floor is due."""
        if not self.floor_hz or now < self._next_floor:
            return 0
        self._next_floor = now + 1.0 / self.floor_hz
        n = 0
        with self._lock:
            for key, sample in list(self._last.items()):
                fresh = (dataclasses.replace(sample, stamp=_now_time())
                         if _has_member(sample, "stamp") else sample)
                self._dw.write(fresh)
                self._last[key] = fresh
                n += 1
        return n

    def _exit(self, reason: str) -> None:
        if self.on_exit == "withdraw":
            for key in self.keys():
                self.withdraw(key, reason=reason)
            return
        # "keep", and every volatile or unkeyed lane: the instances stand,
        # and this writer says it is leaving them, explicitly and now. Measured:
        # left to entity deletion at process exit, readers learned of a clean
        # stop only when the lease ran out, ten seconds later; an explicit
        # unregister reached them at once. With autodispose off an unregister
        # is NOT_ALIVE_NO_WRITERS, never a dispose.
        with self._lock:
            for sample in self._last.values():
                self._dw.unregister_instance(sample)

    def _flush(self, timeout_s: float) -> bool:
        return bool(self._dw.wait_for_acks(duration(seconds=timeout_s)))

    def topic_meta(self) -> Dict[str, Any]:
        """This lane as one `TopicMeta`, through the web layer's builder."""
        p = self.profile
        segs = self.topic.split("/")
        where = (dict(name=self.topic) if self._lane.explicit else
                 dict(stream=segs[2], version=segs[4]))
        return records.topic(
            self.type_name, scene=self._p.scene, qos_profile=p.name,
            durability=None if p.registered else p.durability,
            rate_hz=float(self.floor_hz or 0.0), **where)


class Reader:
    """One lane's reader. Created by `Participant.reader`, never directly."""

    def __init__(self, p: "Participant", lane: _Lane, settle_s: float,
                 ignore_local: bool = False) -> None:
        self._p, self._lane, self._settle = p, lane, settle_s
        self._dr = DataReader(p._dp, p._topic(lane),
                              qos=reader_qos(lane.profile, ignore_local))
        self._keys: Dict[int, str] = {}
        #: Per key: "alive", "quiet" (no writers) or "gone" (disposed). A
        #: transition is an event, emitted once.
        self._state: Dict[str, str] = {}
        self._reason: Dict[str, str] = {}
        # The replay's progress, advanced by `take()` without blocking. A
        # VOLATILE lane has nothing to replay, so its marker is due at once.
        self._born = time.time()
        self._matched, self._matched_since = -1, self._born
        self._replayed = 0
        self._marked = False

    @property
    def topic(self) -> str:
        return self._lane.topic

    def matched(self) -> int:
        """How many writers this reader is matched with, now."""
        return self._dr.get_subscription_matched_status().current_count

    def wait_matched(self, n: int = 1, timeout: float = 5.0) -> bool:
        """Wait until at least `n` writers have matched. False on timeout."""
        return _wait(lambda: self.matched() >= n, timeout)

    def _replay_complete(self, now: float) -> bool:
        """Has the latched replay finished? Never blocks.

        Two conditions, in order. The matched-writer count has stopped
        changing for `settle_s`: discovery is asynchronous, so a reader that
        asked for history the instant it was created would be told it was
        complete before a single writer had matched. Then the matched
        writers' history has arrived. Each has a deadline, so a writer that
        never delivers cannot hold the marker forever.
        """
        if not self._lane.profile.latched:
            return True
        n = self.matched()
        if n != self._matched:
            self._matched, self._matched_since = n, now
        settle_by = self._born + max(self._settle * 6, 3.0)
        if now - self._matched_since < self._settle and now < settle_by:
            return False
        if self._matched <= 0:
            return True
        return (bool(self._dr.wait_for_historical_data(duration(seconds=0)))
                or now >= settle_by + 5.0)

    def _transition(self, key: str, state: Any, out: List[Any]) -> None:
        """The instance state DDS reports, as at most one event per change.

        DDS reports a disposal or a loss of writers two ways. It may queue an
        invalid sample, or, when the instance's last valid sample has not
        been taken yet, put the new state on that valid sample. Under
        KEEP_LAST(1) the second is the usual case for a withdrawal, whose
        terminal sample and dispose arrive together. A reader that looked
        only at invalid samples would lose every such removal, so both are
        read here, and a transition is emitted once whichever way it came.
        """
        prev = self._state.get(key)
        if state == InstanceState.NotAliveDisposed:
            if prev != "gone":
                out.append(Dispose(self.topic, key, self._reason.pop(key, "")))
            self._state[key] = "gone"
        elif state == InstanceState.NotAliveNoWriters:
            if prev == "gone":
                return          # removed already; its writer leaving says nothing
            if prev != "quiet":
                out.append(Liveliness(self.topic, key, False))
            self._state[key] = "quiet"
        else:
            self._state[key] = "alive"

    def _events(self) -> List[Any]:
        out: List[Any] = []
        for s in self._dr.take(N=256) or []:
            info = getattr(s, "sample_info", None)
            if info is None:
                continue
            handle, state = info.instance_handle, info.instance_state
            if info.valid_data:
                key = codec.key_of(s)
                self._keys[handle] = key
                if self._state.get(key) == "quiet":
                    out.append(Liveliness(self.topic, key, True))
                if _has_member(s, "state_reason") and s.state_reason:
                    self._reason[key] = s.state_reason
                out.append(Sample(self.topic, key, s))
                self._state[key] = "alive"
                self._transition(key, state, out)
                continue
            self._transition(self._keys.get(handle, ""), state, out)
        return out

    def take(self) -> List[Any]:
        """Every event that has arrived, now, without blocking.

        For a program that already owns its loop: a tick, a framework's
        timer, a request handler. The events are the ones `events()` yields,
        in the same order, with exactly one `EndOfReplay`. It comes on the
        first `take()` at which the replay is complete, after every replayed
        sample, so the count it carries is what was replayed. Until then,
        samples are replay. After it, they are live.

        Whether the replay is complete is decided before this call drains
        the reader. History that arrived by then is taken in this call,
        ahead of the marker, and none can land behind it.

        Use `take()` or `events()` on a reader, not both: they share one
        drain.
        """
        due = not self._marked and self._replay_complete(time.time())
        evs = self._events()
        if not self._marked:
            self._replayed += sum(isinstance(e, Sample) for e in evs)
            if due:
                evs.append(EndOfReplay(self.topic, self._replayed))
                self._marked = True
        return evs

    def events(self, timeout: Optional[float] = None, poll: float = 0.02,
               stop: Optional[threading.Event] = None) -> Iterator[Any]:
        """Latched replay, exactly one `EndOfReplay`, then live.

        `EndOfReplay` means "complete for the writers matched when it fired".
        The matched set is allowed to settle first, then history is awaited.
        A latched writer discovered later is just more `Sample`s after the
        marker, because DDS states nothing more. A VOLATILE lane has nothing
        to replay, and emits the marker at once with `replayed=0`.

        `timeout` ends the stream after that many seconds without an event,
        once the replay is over, as the web client's `subscribe(timeout)`
        does; None runs until the caller stops iterating. `stop`, a
        `threading.Event`, ends it when set, for a program draining readers
        on threads of its own.

        This is `take()` in a loop, so the two cannot disagree.
        """
        quiet_since = None
        while stop is None or not stop.is_set():
            evs = self.take()
            for ev in evs:
                yield ev
            if self._marked:
                now = time.time()
                if evs or quiet_since is None:
                    quiet_since = now
                elif timeout is not None and now - quiet_since >= timeout:
                    return
            time.sleep(poll)


RELAYED_BY = "relayed_by"


class AnnounceRelay:
    """Re-publishes other services' announces, marked as relayed. From `p.relay()`.

    A bridge or a replayer carries announces it heard somewhere else: over
    MQTT, from a recording, from another bus. The claim is still the
    asserter's. So the relay is explicit about what it is doing:

    * **Its own writer.** The relay writes with a DataWriter of its own, never
      the asserter's identity and never the participant's own announce
      writer.
    * **Marked.** Every relayed announce carries a `relayed_by` hint naming
      the relaying participant's `service_id`. Its other members are the
      asserter's, unchanged.
    * **Refused when it would blur who said what:**
      - one of this participant's own services, which it announces with
        `announce()`;
      - an announce already relayed, because relays do not chain;
      - an announce whose hints are full.
    * **It ends only what it started.** On `close()` the relay unregisters
      what it carried: readers see its writer leave, never a removal, because
      the asserter's presence is not the relay's to end. `withdraw()` is for
      a departure the relay itself observed where it heard the announce. It
      takes a reason, and it removes only the relayed copy.
    """

    def __init__(self, p: "Participant") -> None:
        self._p = p
        wk = registry.well_known("Announce")
        self._cls = _types_class(wk.fqn)
        self._dw = DataWriter(p._dp, p._topic(p._wk_lane("Announce")),
                              qos=writer_qos(wk.profile))
        self._last: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def write(self, announce: Any) -> Any:
        """Relay one announce, typed or as its N.1 document. Returns what was sent."""
        doc = codec.to_json(announce) if not isinstance(announce, dict) \
            else dict(announce)
        sid = doc.get("service_id", "")
        problems = []
        if sid in self._p._services or sid == self._p.service_id:
            problems.append(f"{sid} is this participant's own service: "
                            f"announce() it, do not relay it")
        hints = [dict(h) for h in doc.get("hints") or []]
        prior = [h["value"] for h in hints if h.get("key") == RELAYED_BY]
        if prior:
            problems.append(f"{sid}'s announce was already relayed by "
                            f"{prior[0]}: a relay re-publishes the asserter's "
                            f"announce, not another relay's")
        if len(hints) >= 32:
            problems.append(f"{sid}'s announce has 32 hints, the type's bound, "
                            f"so it cannot be marked as relayed")
        if problems:
            raise ParticipantRefused(problems)
        hints.append({"key": RELAYED_BY, "value": self._p.service_id})
        doc["hints"] = hints
        sample = codec.from_json(self._cls, doc)
        codec.validate(sample)
        with self._lock:
            self._dw.write(sample)
            self._last[sid] = sample
        self._p._recorded(registry.well_known("Announce").topic,
                          registry.well_known("Announce").fqn, sample)
        return sample

    def withdraw(self, service_id: str, *, reason: str) -> None:
        """Remove the relayed copy of a service the relay saw depart."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("withdraw() needs a non-empty reason=")
        wk = registry.well_known("Announce")
        with self._lock:
            if service_id not in self._last:
                raise KeyError(f"this relay carries no announce from "
                               f"{service_id!r}")
            terminal = dataclasses.replace(self._last.pop(service_id),
                                           stamp=_now_time())
            self._dw.write(terminal)
            self._dw.dispose(terminal)
        self._p._recorded(wk.topic, wk.fqn, terminal)
        self._p._recorded_dispose(wk.topic, service_id,
                                  f"relayed by {self._p.service_id}: {reason}")

    def _exit(self) -> None:
        with self._lock:
            for sample in self._last.values():
                self._dw.unregister_instance(sample)


class Participant:
    """One DDS participant on one scene. See the module docstring."""

    def __init__(self, scene: str, *, domain: Optional[int] = None,
                 service_id: Optional[str] = None,
                 authority: Optional[str] = None,
                 manifest_uri: Optional[str] = None) -> None:
        problems: List[str] = []
        if not isinstance(scene, str) or not registry.SEGMENT.match(scene):
            problems.append(f"scene {scene!r} is not a §3.3.1 segment "
                            f"({registry.SEGMENT.pattern}); there is no "
                            f"default scene")
        source = "domain="
        if domain is None and os.environ.get(DOMAIN_ENV, "") != "":
            source, domain = DOMAIN_ENV, os.environ[DOMAIN_ENV]
        if domain is None:
            problems.append(f"no domain: pass domain= or set {DOMAIN_ENV}. "
                            f"DDS's 0 is not a choice anyone made")
        else:
            try:
                domain = int(domain)
                if not 0 <= domain <= 232:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"{source} {domain!r} is not a DDS domain id "
                                f"(0..232)")
        if service_id is not None and (not isinstance(service_id, str)
                                       or not service_id.strip()):
            problems.append("service_id= must be a non-empty string when given")
        if problems:
            raise ParticipantRefused(problems)
        self.scene, self.domain = scene, int(domain)
        self.service_id, self.authority = service_id, authority
        self.manifest_uri = manifest_uri
        self._dp = DomainParticipant(self.domain)
        self._topics: Dict[str, Topic] = {}
        self._writers: List[Writer] = []
        self._readers: List[Reader] = []
        #: Every service this participant announces, by service_id: what it
        #: said, its own writer, the sample as last published (None until one
        #: has been), and when its refresh is due.
        self._services: Dict[str, Dict[str, Any]] = {}
        self._depart_w: Optional[DataWriter] = None
        self._relay: Optional["AnnounceRelay"] = None
        self._recorders: List[Any] = []
        self._closed = False

    # -- endpoints ---------------------------------------------------------
    def _topic(self, lane: _Lane) -> Topic:
        # One Topic per name per participant: cyclonedds refuses a second.
        # Created with default QoS, so a second library's endpoints can share
        # the topic with their own policies (spatialdds_ros's pattern).
        t = self._topics.get(lane.topic)
        if t is None:
            t = self._topics[lane.topic] = Topic(self._dp, lane.topic, lane.cls)
        return t

    def writer(self, type_: Union[str, type], *, stream: Optional[str] = None,
               qos_profile: Union[str, Profile, None] = None,
               on_exit: Optional[str] = None,
               floor_hz: Optional[float] = None, validate: bool = True,
               version: str = "v1", type_name: Optional[str] = None,
               lifespan_sec: Optional[float] = None,
               topic: Optional[str] = None) -> Writer:
        """A writer for a registered type, a well-known topic, or an extension.

        `topic=` gives the whole name instead of building it from this
        participant's scene, for another scene's lane or one the spec names
        only by example (`registry.check_topic` says what is accepted).
        """
        self._open()
        problems: List[str] = []
        if not self.service_id:
            problems.append("a writer needs service_id= on the participant: a "
                            "participant that writes says who it is when it "
                            "announces")
        if isinstance(type_, str) and type_ in _OWNED:
            problems.append(f"{type_} is the participant's own: use "
                            f"announce(), and close() departs")
        lane = _Lane(self.scene, type_, type_name=type_name, stream=stream,
                     qos_profile=qos_profile, version=version,
                     problems=problems, topic=topic)
        if lane.profile is not None and lane.fqn:
            keyed = codec.key_member(lane.fqn) is not None
            if lane.profile.latched and keyed:
                if on_exit not in ON_EXIT:
                    problems.append(
                        f"{lane.topic} is a latched keyed lane, so its "
                        f"instances outlive this writer: say what a clean "
                        f"stop does to them, on_exit='keep' or "
                        f"on_exit='withdraw' (got {on_exit!r})")
            elif on_exit is not None:
                problems.append(
                    f"{lane.topic} is {lane.profile.durability.lower()}"
                    f"{'' if keyed else ' and unkeyed'}: nothing outlives its "
                    f"writer to keep or withdraw, so on_exit= is not accepted")
            if floor_hz is not None:
                if floor_hz <= 0:
                    problems.append(f"floor_hz {floor_hz} must be positive")
                elif lane.profile.deadline_ms and \
                        floor_hz < 1000.0 / lane.profile.deadline_ms:
                    problems.append(
                        f"floor_hz {floor_hz} cannot keep {lane.profile.name}'s "
                        f"{lane.profile.deadline_ms} ms deadline "
                        f"(needs >= {1000.0 / lane.profile.deadline_ms:g} Hz); "
                        f"a writer does not offer a contract it will not meet")
        if problems:
            raise ParticipantRefused(problems)
        w = Writer(self, lane, on_exit, floor_hz, validate, lifespan_sec)
        self._writers.append(w)
        for sid, svc in self._services.items():
            if svc["spec"]["topics"] is None:
                self._publish_announce(sid)   # its topics changed: §3.3
        return w

    def reader(self, type_: Union[str, type], *, stream: Optional[str] = None,
               qos_profile: Union[str, Profile, None] = None,
               version: str = "v1", type_name: Optional[str] = None,
               settle_s: float = 1.0, topic: Optional[str] = None,
               history: Union[str, int, None] = None,
               ignore_local: bool = False) -> Reader:
        """A reader for a registered type, a well-known topic, or an extension.

        `topic=` is as for `writer()`. `history=` overrides the reader's own
        cache, `"KEEP_ALL"` or a depth: a reply reader that must not lose
        replies arriving together. History is the reader's own policy, so
        matching is unaffected. `ignore_local=True` does not deliver this
        participant's own writes, for a bridge that writes and reads one lane.
        """
        self._open()
        problems: List[str] = []
        lane = _Lane(self.scene, type_, type_name=type_name, stream=stream,
                     qos_profile=qos_profile, version=version,
                     problems=problems, topic=topic)
        if history is not None and lane.profile is not None:
            if history == "KEEP_ALL":
                lane.profile = dataclasses.replace(lane.profile,
                                                   history="KEEP_ALL")
            elif isinstance(history, int) and not isinstance(history, bool) \
                    and history >= 1:
                lane.profile = dataclasses.replace(
                    lane.profile, history="KEEP_LAST", depth=history)
            else:
                problems.append(f"history={history!r}: 'KEEP_ALL' or a depth "
                                f">= 1")
        if problems:
            raise ParticipantRefused(problems)
        r = Reader(self, lane, settle_s, ignore_local)
        self._readers.append(r)
        return r

    # -- presence ----------------------------------------------------------
    def announce(self, kind: str, *, coverage: Any, ttl_sec: int = 300,
                 name: str = "", org: str = "", version: str = "",
                 hints: Optional[Dict[str, str]] = None,
                 caps: Optional[Dict[str, Any]] = None,
                 manifest_uri: Optional[str] = None,
                 transforms: Optional[List[Dict[str, Any]]] = None,
                 service_id: Optional[str] = None,
                 topics: Optional[List["Writer"]] = None) -> Dict[str, Any]:
        """Announce a service and its lanes. Returns the announce's N.1 form.

        Built by the web layer's `records.service_announce`, so there is one
        builder for both layers. The announce is refreshed every
        `ttl_sec / 3` by `tick()` and rides Lifespan `ttl_sec`.

        * **Its lanes.** `topics=None` is every writer this participant has
          opened, and the announce is re-published when one is added. A list
          of writers is exactly those lanes. Either way the rows are the
          writers' own `TopicMeta`, never written by hand.
        * **Its placement.** `transforms=` are the service's frame
          transforms, as N.1 `Transform` documents: the slot an AR client
          places content with.
        * **More than one service.** `service_id=` another id announces
          another service from this participant: an operator console
          speaking for several platforms, say. Each has its own writer, so
          its own TTL and liveliness, and each departs on `close()`. Once a
          participant announces more than one, each must name its lanes with
          `topics=`, because "every writer" no longer says whose they are.

        Calling it again for a service already announced replaces what that
        service says.
        """
        self._open()
        sid = service_id or self.service_id
        problems = []
        if not self.service_id:
            problems.append("announce() needs service_id= on the participant")
        if service_id is not None and (not isinstance(service_id, str)
                                       or not service_id.strip()):
            problems.append("service_id= must be a non-empty string when given")
        muri = manifest_uri or (self.manifest_uri if sid == self.service_id
                                else None)
        if not (muri or self.authority):
            problems.append("announce() needs authority= on the participant "
                            "(or manifest_uri=): a spatialdds:// URI names a "
                            "DNS authority somebody owns, and this layer will "
                            "not invent one")
        if not isinstance(ttl_sec, int) or ttl_sec < 3:
            problems.append(f"ttl_sec {ttl_sec!r} must be an integer >= 3, so "
                            f"the refresh at ttl/3 is at least a second apart")
        if topics is not None:
            mine = set(map(id, self._writers))
            for w in topics:
                if not isinstance(w, Writer) or id(w) not in mine:
                    problems.append(f"topics= takes this participant's own "
                                    f"writers, not {w!r}")
        others = {k: v for k, v in self._services.items() if k != sid}
        if others and (topics is None or any(
                v["spec"]["topics"] is None for v in others.values())):
            problems.append(
                f"this participant announces {', '.join(sorted(others))} as "
                f"well as {sid}: once it speaks for more than one service, "
                f"each announce names its lanes with topics=")
        if problems:
            raise ParticipantRefused(problems)
        svc = self._services.get(sid)
        if svc is None:
            if self._depart_w is None:
                self._depart_w = DataWriter(
                    self._dp, self._topic(self._wk_lane("Depart")),
                    qos=writer_qos(registry.well_known("Depart").profile))
            svc = self._services[sid] = {
                "w": DataWriter(
                    self._dp, self._topic(self._wk_lane("Announce")),
                    qos=writer_qos(registry.well_known("Announce").profile,
                                   lifespan_sec=ttl_sec)),
                "last": None, "next": 0.0}
        svc["spec"] = dict(kind=kind, coverage=coverage, ttl_sec=ttl_sec,
                           name=name, org=org, version=version, hints=hints,
                           caps=caps, manifest_uri=muri,
                           transforms=list(transforms or []),
                           topics=None if topics is None else list(topics))
        return self._publish_announce(sid)

    def _wk_lane(self, message: str) -> _Lane:
        return _Lane(self.scene, message, type_name=None, stream=None,
                     qos_profile=None, version="v1", problems=[])

    def _announce_sample(self, sid: str):
        a = self._services[sid]["spec"]
        lanes = self._writers if a["topics"] is None else a["topics"]
        doc = records.service_announce(
            sid, a["kind"], coverage=a["coverage"],
            authority=self.authority or "", scene=self.scene,
            name=a["name"], org=a["org"], version=a["version"],
            topics=[w.topic_meta() for w in lanes
                    if w._lane.well_known is None],
            manifest_uri=a["manifest_uri"], ttl_sec=a["ttl_sec"],
            hints=a["hints"], caps=a["caps"])
        doc["transforms"] = [dict(t) for t in a["transforms"]]
        cls = _types_class(registry.well_known("Announce").fqn)
        return doc, codec.from_json(cls, doc)

    def _publish_announce(self, sid: str) -> Dict[str, Any]:
        svc = self._services[sid]
        doc, sample = self._announce_sample(sid)
        codec.validate(sample)
        svc["w"].write(sample)
        svc["last"] = sample
        svc["next"] = time.time() + svc["spec"]["ttl_sec"] / 3.0
        self._recorded(registry.well_known("Announce").topic,
                       registry.well_known("Announce").fqn, sample)
        return doc

    def _depart(self, reason: str) -> None:
        wk = registry.well_known("Announce")
        Depart = _types_class(registry.well_known("Depart").fqn)
        for sid, svc in self._services.items():
            # Nothing published, nothing to depart: an announce() that failed
            # on its first publish leaves no presence, and must not turn
            # close() into an error that hides why it failed.
            if svc["last"] is None:
                continue
            terminal = dataclasses.replace(svc["last"], stamp=_now_time())
            svc["w"].write(terminal)
            svc["w"].dispose(terminal)
            self._recorded(wk.topic, wk.fqn, terminal)
            self._recorded_dispose(wk.topic, sid, reason)
            dep = Depart(service_id=sid, stamp=_now_time())
            self._depart_w.write(dep)
            self._recorded(registry.well_known("Depart").topic,
                           registry.well_known("Depart").fqn, dep)
            log.info("departed %s: %s", sid, reason)

    def relay(self) -> "AnnounceRelay":
        """The writer a bridge re-publishes other services' announces with.

        Explicit re-publication under this participant's own identity, never
        impersonation. See `AnnounceRelay`.
        """
        self._open()
        if not self.service_id:
            raise ParticipantRefused([
                "relay() needs service_id= on the participant: a relayed "
                "announce says who relayed it"])
        if self._relay is None:
            self._relay = AnnounceRelay(self)
        return self._relay

    # -- the program's clock -------------------------------------------------
    def tick(self, now: Optional[float] = None) -> Dict[str, int]:
        """Refresh the announce if due, and run every writer's floor."""
        self._open()
        now = time.time() if now is None else now
        out = {"announce": 0, "floor": 0}
        for sid, svc in self._services.items():
            if now >= svc["next"]:
                self._publish_announce(sid)
                out["announce"] += 1
        for w in self._writers:
            out["floor"] += w._floor(now)
        return out

    def loop(self, hz: float) -> Iterator[int]:
        """Tick at `hz` until SIGINT or SIGTERM. Yields the iteration number.

        The signal handlers are installed only while this iterates, and the
        previous ones restored after, so a library does not take a program's
        signals for longer than the program asked.
        """
        if hz <= 0:
            raise ValueError(f"loop(hz={hz}) needs a positive rate")
        stop = threading.Event()
        old = {}
        main = threading.current_thread() is threading.main_thread()
        if main:
            for sig in (signal.SIGINT, signal.SIGTERM):
                old[sig] = signal.signal(sig, lambda *_: stop.set())
        try:
            i = 0
            while not stop.is_set():
                start = time.time()
                self.tick(start)
                yield i
                i += 1
                stop.wait(max(0.0, 1.0 / hz - (time.time() - start)))
        finally:
            for sig, h in old.items():
                signal.signal(sig, h)

    # -- the recording tap -----------------------------------------------------
    def record(self, path: Any) -> Any:
        """Record everything this participant writes from now on, as typed MCAP.

        Samples, withdrawals with their reasons, and the departure. Finished
        when the participant closes. Returns the `Recorder`.
        """
        self._open()
        from .record import Recorder
        r = Recorder(path, scene=self.scene, principal=self.service_id or "")
        self._recorders.append(r)
        for svc in self._services.values():
            if svc["last"] is not None:
                self._recorded(registry.well_known("Announce").topic,
                               registry.well_known("Announce").fqn,
                               svc["last"])
        return r

    def _recorded(self, topic: str, fqn: str, sample: Any) -> None:
        for r in self._recorders:
            r.sample(topic, fqn, sample)

    def _recorded_dispose(self, topic: str, key: str, reason: str) -> None:
        for r in self._recorders:
            r.dispose(topic, key, reason)

    # -- stopping ------------------------------------------------------------
    def _open(self) -> None:
        if self._closed:
            raise RuntimeError("this participant is closed")

    def close(self, reason: str = "the participant stopped",
              flush_s: float = 2.0) -> None:
        """Stop cleanly: each writer's on_exit, the announce departs, acks awaited.

        Idempotent. A participant that is never closed (killed, or simply
        dropped) withdraws nothing: its readers see liveliness end.
        """
        if self._closed:
            return
        try:
            for w in self._writers:
                w._exit(reason)
            self._depart(reason)
            if self._relay is not None:
                self._relay._exit()
            for w in self._writers:
                w._flush(flush_s)
            for dw in self._presence_writers():
                dw.wait_for_acks(duration(seconds=flush_s))
            for r in self._recorders:
                r.flush()
        finally:
            self._closed = True
            # `__del__` is cyclonedds's own deletion: it drops the entity from
            # the binding's registry and deletes it, idempotently. (`_delete`
            # takes the entity handle, and called bare it raises; an earlier
            # draft of this method swallowed that and deleted nothing.)
            for e in ([w._dw for w in self._writers]
                      + [r._dr for r in self._readers]
                      + self._presence_writers()
                      + list(self._topics.values()) + [self._dp]):
                try:
                    e.__del__()
                except Exception as err:          # pragma: no cover
                    log.warning("could not delete %r: %s", e, err)

    def _presence_writers(self) -> List[DataWriter]:
        out = [svc["w"] for svc in self._services.values()]
        if self._depart_w is not None:
            out.append(self._depart_w)
        if self._relay is not None:
            out.append(self._relay._dw)
        return out

    def __enter__(self) -> "Participant":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def participant(scene: str, **kw: Any) -> Participant:
    """`sd.participant(scene, domain=…, service_id=…, authority=…)`."""
    return Participant(scene, **kw)
