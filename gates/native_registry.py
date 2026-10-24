#!/usr/bin/env python3
"""The registry and QoS table are the spec's own text at the pin, and refuse to guess.

`artifacts/registry.json` is what `tools/gen_registry.py` reads out of
`sections/v1.8/02-idl-profiles.md` at the pinned tag: §3.3.3's profiles,
§3.3.2's rows and the well-known discovery topics. This gate:

* regenerates it from a SpatialDDS-spec checkout and requires byte equality.
  `gates/run_all.py` skips the gate with the reason where there is no
  checkout, or the checkout lacks the tag;
* requires every row's IDL type to be one the vendored IDL defines;
* requires the profile count to be §3.3.3's (19 at v1.8) and the type count
  §3.3.2's (37). Both are read from the regenerated text, not typed here;
* shows the refusals happen: a row naming no profile is refused without one,
  with the profiles that fit; a profile contradicting what a row spells is
  refused; a stream contradicting a row's topic template is refused;
* holds `topic_name` to the registry's templates for every templated row.

Usage:  gates/native_registry.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                    # noqa: E402

import gen_registry                                           # noqa: E402
import spatialdds as sd                                       # noqa: E402
from spatialdds.dds import registry                           # noqa: E402
from spatialdds.server import idlmodel, pin                   # noqa: E402


def refused(fn, *a, **k) -> str:
    try:
        fn(*a, **k)
    except registry.RegistryError as e:
        return str(e)
    return ""


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    fresh = gen_registry.dump(gen_registry.build(sd.SPEC_TAG))
    have = registry.REGISTRY.read_text()
    checks.append((f"artifacts/registry.json is what the generator reads from "
                   f"the spec at {sd.SPEC_TAG}, byte for byte",
                   fresh == have, "regenerate with python3 tools/gen_registry.py"))
    checks.append((f"…which is the commit the package pins "
                   f"({sd.SPEC_COMMIT})",
                   registry.pin()["commit"].startswith(sd.SPEC_COMMIT),
                   registry.pin()["commit"]))

    text = gen_registry.spec_text(sd.SPEC_TAG)
    n_prof = sum(1 for line in gen_registry._section(
        text, "#### **3.3.3 QoS Profiles**", "###### Notes")
        if line.startswith("| `"))
    n_type = sum(1 for line in gen_registry._section(
        text, "#### **3.3.2 Typed Topics Registry**",
        "##### **Informative Example Registrations**")
        if line.startswith("| `"))
    checks.append((f"every §3.3.3 row is a profile ({n_prof} rows in the text)",
                   len(registry.profiles()) == n_prof,
                   f"{len(registry.profiles())}"))
    checks.append((f"every §3.3.2 row is a registration ({n_type} rows)",
                   len(registry.registrations()) == n_type,
                   f"{len(registry.registrations())}"))

    files = sorted(p.relative_to(pin.IDL_DIR).as_posix()
                   for p in pin.IDL_DIR.rglob("*.idl"))
    order: list[str] = []

    def visit(f: str) -> None:
        if f in order:
            return
        for inc in re.findall(r'^#include\s+"([^"]+)"',
                              (pin.IDL_DIR / f).read_text(), re.M):
            if inc in files:
                visit(inc)
        order.append(f)
    for f in files:
        visit(f)
    model = idlmodel.parse(pin.flatten(pin.IDL_DIR, order))
    undefined = sorted(r.type_name for r in registry.registrations().values()
                       if r.fqn not in model.aggregates)
    undefined += sorted(w.message for w in registry.well_known().values()
                        if w.fqn not in model.aggregates)
    checks.append(("every registered and well-known type is defined by the "
                   "vendored IDL", not undefined, f"{undefined}"))

    unnamed = sorted(r.type_name for r in registry.registrations().values()
                     if r.profile is None)
    msg = refused(registry.resolve_profile,
                  registry.registration("model_entity"), None)
    checks.append((f"a row that names no profile is refused without one "
                   f"({len(unnamed)} such rows: {', '.join(unnamed)})",
                   "names no QoS profile" in msg and "MAP_META" in msg,
                   msg[:160]))
    ok = registry.resolve_profile(registry.registration("model_entity"),
                                  "MAP_META")
    checks.append(("…and accepted with one that carries what the row spells",
                   ok.name == "MAP_META", ""))
    clash = refused(registry.resolve_profile,
                    registry.registration("model_entity"), "POSE_RT")
    checks.append(("a profile contradicting what a row spells is refused, "
                   "naming the clash",
                   "contradicts" in clash and "durability" in clash, clash[:160]))
    checks.append(("a row's own profile is the default where it names one",
                   registry.resolve_profile(registry.registration("geopose"),
                                            None).name == "POSE_RT", ""))
    bad_profile = refused(registry.profile, "MODEL_LATCHED")
    checks.append(("an unregistered profile name is refused rather than "
                   "invented", "not a §3.3.3 profile" in bad_profile, ""))

    templated = {t: r for t, r in registry.registrations().items()
                 if r.topic_template}
    wrong = [t for t, r in templated.items()
             if registry.topic_name("plaza", None, t)
             != r.topic_template.replace("<scene>", "plaza")]
    checks.append((f"topic_name follows every row's template "
                   f"({', '.join(sorted(templated))})", not wrong, f"{wrong}"))
    checks.append(("a stream contradicting a template is refused",
                   "would name another topic" in refused(
                       registry.topic_name, "plaza", "model", "model_entity"),
                   ""))
    checks.append(("an untemplated type needs a stream, and gets §3.3.1's "
                   "five segments",
                   "pass stream=" in refused(registry.topic_name, "plaza",
                                             None, "geopose")
                   and registry.topic_name("plaza", "robot1", "geopose")
                   == "spatialdds/plaza/robot1/geopose/v1", ""))
    checks.append(("a scene that is not a segment is refused",
                   "not a §3.3.1 segment" in refused(registry.topic_name,
                                                     "a/b", "s", "geopose"),
                   ""))
    # The web layer's TopicMeta builder takes the same rules, so an announce
    # built either way says the same thing.
    pose = sd.topic("geopose", scene="plaza", stream="r", qos_profile="POSE_RT")
    checks.append(("sd.topic derives durability from a registered profile "
                   "(POSE_RT is VOLATILE), where it used to say TRANSIENT_LOCAL",
                   pose["durability"] == "VOLATILE", pose["durability"]))
    try:
        sd.topic("geopose", scene="plaza", stream="r", qos_profile="POSE_RT",
                 durability="TRANSIENT_LOCAL")
        contra = ""
    except ValueError as e:
        contra = str(e)
    checks.append(("…refuses a durability that contradicts the profile",
                   "contradicts POSE_RT" in contra, contra[:120]))
    checks.append(("…names a templated type by its template",
                   sd.topic("model_entity", scene="plaza",
                            qos_profile="MAP_META")["name"]
                   == "spatialdds/plaza/world/entity/v1", ""))
    checks.append(("…and leaves an extension profile's durability empty "
                   "(absent), rather than guessing",
                   sd.topic("x.depth", scene="plaza", stream="c",
                            qos_profile="DEPTH_LIVE")["durability"] == "", ""))
    plain = sd.topic("geo_anchor", scene="plaza", stream="frames")
    checks.append(("…and keeps TRANSIENT_LOCAL for a row with no profile, "
                   "the definitions a registry holds",
                   plain["durability"] == "TRANSIENT_LOCAL"
                   and plain["name"] == "spatialdds/plaza/frames/geo_anchor/v1",
                   ""))
    return report("registry", checks,
                  "the registry is the spec's text at the pin, and it refuses "
                  "to guess")


if __name__ == "__main__":
    sys.exit(main())
