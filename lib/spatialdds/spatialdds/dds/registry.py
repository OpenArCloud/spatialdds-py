"""The §3.3.2 registry, the §3.3.3 QoS table and the well-known topics, as data.

    from spatialdds.dds import registry
    registry.profile("POSE_RT")            # Profile(reliability='BEST_EFFORT', …)
    registry.registration("geopose")       # its IDL type and its profile
    registry.topic_name("plaza", "robot1", "geopose")

Read from `artifacts/registry.json`, which `tools/gen_registry.py` generates
from the specification text at the package's pin. A gate fails if a
regeneration disagrees. Nothing here is transcribed by hand: four codebases did
that and every copy drifted (docs/NATIVE-SDK-CENSUS.md, subject 2).

Pure data and the standard library, so it needs no extra.

**What this refuses to guess.** A registry row that names no profile, such as
`navsat_status` or `model_entity`, gets no default here. Some of those rows
spell some policies in prose instead (`model_entity`: RELIABLE +
TRANSIENT_LOCAL, KEEP_LAST(1)), and four §3.3.3 profiles carry exactly those
values. Choosing one would be a claim the spec does not make. So
`resolve_profile` requires the caller to name one, checks it against whatever
the row does spell, and says which profiles fit when it refuses.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

__all__ = ["Profile", "Registration", "WellKnown", "RegistryError",
           "profiles", "profile", "registrations", "registration",
           "well_known", "resolve_profile", "topic_name", "SEGMENT", "pin"]

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
REGISTRY = ARTIFACTS / "registry.json"
#: §3.3.1 segments, and the rule a scene or stream must meet to be one.
SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RegistryError(ValueError):
    """A type, profile or topic this registry cannot name, with the reason."""


@dataclass(frozen=True)
class Profile:
    """One §3.3.3 row, or a documented deployment extension of the same shape.

    The four normative columns, and nothing else: "Other DDS policies MAY be
    tuned per deployment" (§3.3.3), so the native layer sets those itself.
    """
    name: str
    reliability: str            # RELIABLE | BEST_EFFORT
    durability: str             # TRANSIENT_LOCAL | VOLATILE
    history: str = "KEEP_LAST"  # KEEP_LAST | KEEP_ALL
    depth: int = 1
    deadline_ms: Optional[int] = None
    registered: bool = False

    def __post_init__(self) -> None:
        bad = []
        if self.reliability not in ("RELIABLE", "BEST_EFFORT"):
            bad.append(f"reliability {self.reliability!r}")
        if self.durability not in ("TRANSIENT_LOCAL", "VOLATILE"):
            bad.append(f"durability {self.durability!r}")
        if self.history not in ("KEEP_LAST", "KEEP_ALL"):
            bad.append(f"history {self.history!r}")
        if self.history == "KEEP_LAST" and self.depth < 1:
            bad.append(f"depth {self.depth}")
        if self.deadline_ms is not None and self.deadline_ms <= 0:
            bad.append(f"deadline_ms {self.deadline_ms}")
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", self.name or ""):
            bad.append(f"name {self.name!r}")
        if bad:
            raise RegistryError(f"profile {self.name!r}: " + ", ".join(bad))

    @property
    def latched(self) -> bool:
        return self.durability == "TRANSIENT_LOCAL"

    def satisfies(self, spelled: Mapping[str, Any]) -> list[str]:
        """The policies a registry row spells that this profile contradicts."""
        out = []
        for k in ("reliability", "durability"):
            if k in spelled and spelled[k] != getattr(self, k):
                out.append(f"{k} {spelled[k]} (the profile has "
                           f"{getattr(self, k)})")
        h = spelled.get("history")
        if h and (h["kind"] != self.history
                  or (h["kind"] == "KEEP_LAST" and h["depth"] != self.depth)):
            out.append(f"history {h['kind']}({h.get('depth', '')}) (the "
                       f"profile has {self.history}({self.depth}))")
        return out


@dataclass(frozen=True)
class Registration:
    """One §3.3.2 row."""
    type_name: str
    idl: str                         # "spatial::core::GeoPose"
    profile: Optional[str]           # the §3.3.3 profile the row names, if any
    topic_template: Optional[str]    # "spatialdds/<scene>/world/entity/v1"
    spelled_qos: Mapping[str, Any] = field(default_factory=dict)
    provisional: bool = False

    @property
    def fqn(self) -> str:
        """The dotted identity, as generated classes and the gateway spell it."""
        return self.idl.replace("::", ".")


@dataclass(frozen=True)
class WellKnown:
    """A well-known discovery topic: its message type, name and QoS defaults."""
    message: str                     # "Announce"
    idl: str
    topic: str                       # "spatialdds/discovery/announce/v1"
    profile: Profile

    @property
    def fqn(self) -> str:
        return self.idl.replace("::", ".")


@lru_cache(maxsize=1)
def _doc() -> Dict[str, Any]:
    return json.loads(REGISTRY.read_text())


def pin() -> Dict[str, str]:
    """The spec tag, commit and source section the registry was generated from."""
    return dict(_doc()["spec"])


def _profile(name: str, row: Mapping[str, Any], registered: bool) -> Profile:
    h = row["history"]
    return Profile(name, row["reliability"], row["durability"], h["kind"],
                   int(h.get("depth", 1)), row.get("deadline_ms"), registered)


@lru_cache(maxsize=1)
def profiles() -> Dict[str, Profile]:
    """Every §3.3.3 profile, by name."""
    return {n: _profile(n, r, True) for n, r in _doc()["profiles"].items()}


def profile(name: str) -> Profile:
    try:
        return profiles()[name]
    except KeyError:
        raise RegistryError(
            f"{name!r} is not a §3.3.3 profile at {pin()['tag']}. Registered: "
            f"{', '.join(sorted(profiles()))}. A deployment extension is a "
            f"Profile(...) object, named per §3.3.2's pattern (`DEPTH_LIVE`), "
            f"not a string.") from None


@lru_cache(maxsize=1)
def registrations() -> Dict[str, Registration]:
    """Every §3.3.2 row, by registered type name."""
    return {t: Registration(t, r["idl"], r["profile"], r["topic_template"],
                            r["spelled_qos"], r["provisional"])
            for t, r in _doc()["types"].items()}


def registration(type_name: str) -> Registration:
    try:
        return registrations()[type_name]
    except KeyError:
        raise RegistryError(
            f"{type_name!r} is not a registered type at {pin()['tag']} "
            f"(§3.3.2). A deployment extension is passed as its generated "
            f"class with an explicit Profile.") from None


@lru_cache(maxsize=1)
def _well_known() -> Dict[str, WellKnown]:
    return {m: WellKnown(m, r["idl"], r["topic"],
                         # Named the way the demo named them; never
                         # announced, because a well-known topic is not
                         # advertised in TopicMeta.
                         _profile(f"DISCOVERY_{r['topic'].split('/')[2].upper()}",
                                  r["qos"], True))
            for m, r in _doc()["well_known"].items()}


def well_known(message: Optional[str] = None) -> Union[WellKnown, Dict[str, WellKnown]]:
    """One well-known discovery topic by message type, or all of them."""
    if message is None:
        return dict(_well_known())
    try:
        return _well_known()[message]
    except KeyError:
        raise RegistryError(
            f"{message!r} is not a well-known discovery topic. Known: "
            f"{', '.join(sorted(_well_known()))}") from None


def resolve_profile(reg: Registration,
                    given: Union[str, Profile, None]) -> Profile:
    """The profile a lane of `reg` uses, from the row or from the caller.

    * The row names one, and none is given: that one (the documented default).
    * The row names one, and another is given: the given one. The profile is a
      deployment's choice and §3.3.3's table is the default, not a mandate,
      for rows that name one.
    * The row names none, and none is given: refused, with the profiles that
      carry whatever the row does spell.
    * The row names none, and one is given: it must not contradict anything
      the row spells.
    """
    chosen = profile(given) if isinstance(given, str) else given
    if chosen is None:
        if reg.profile is not None:
            return profile(reg.profile)
        fits = sorted(p.name for p in profiles().values()
                      if not p.satisfies(reg.spelled_qos))
        spelled = ", ".join(f"{k} {v if not isinstance(v, dict) else v['kind'] + '(' + str(v.get('depth', '')) + ')'}"
                            for k, v in sorted(reg.spelled_qos.items())) \
            or "nothing"
        raise RegistryError(
            f"§3.3.2 names no QoS profile for {reg.type_name!r} (it spells "
            f"{spelled}), and the native layer will not pick one: pass "
            f"qos_profile=. Registered profiles consistent with the row: "
            f"{', '.join(fits) or 'none'}; or a Profile(...) extension.")
    clash = chosen.satisfies(reg.spelled_qos)
    if clash:
        raise RegistryError(
            f"{chosen.name} contradicts what §3.3.2 spells for "
            f"{reg.type_name!r}: " + "; ".join(clash))
    return chosen


def topic_name(scene: str, stream: Optional[str], type_name: str,
               version: str = "v1") -> str:
    """The §3.3.1 topic name for a registered type in a scene.

    One function, so a gate holding the names to the registry has one subject.
    A row that gives a topic template (`model_entity` →
    `spatialdds/<scene>/world/entity/v1`) is normative: its stream and type
    segments come from the template, and a different `stream` is refused rather
    than silently ignored.
    """
    if not SEGMENT.match(scene or ""):
        raise RegistryError(f"scene {scene!r} is not a §3.3.1 segment "
                            f"({SEGMENT.pattern})")
    reg = registrations().get(type_name)
    if reg is not None and reg.topic_template:
        name = reg.topic_template.replace("<scene>", scene)
        if "<" in name:
            raise RegistryError(f"{type_name}: template {reg.topic_template!r} "
                                f"has a placeholder this layer does not fill")
        segs = name.split("/")
        if stream is not None and stream != segs[2]:
            raise RegistryError(
                f"§3.3.2 puts {type_name!r} on {reg.topic_template}; stream "
                f"{stream!r} would name another topic")
        if version != segs[4]:
            raise RegistryError(f"§3.3.2 puts {type_name!r} on version "
                                f"{segs[4]}, not {version!r}")
        return name
    if stream is None:
        raise RegistryError(
            f"§3.3.2 gives {type_name!r} no topic template, so its stream is "
            f"the deployment's choice: pass stream=")
    for label, seg in (("stream", stream), ("type", type_name),
                       ("version", version)):
        if not SEGMENT.match(seg or ""):
            raise RegistryError(f"{label} {seg!r} is not a §3.3.1 segment")
    return f"spatialdds/{scene}/{stream}/{type_name}/{version}"
