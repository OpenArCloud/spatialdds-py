#!/usr/bin/env python3
"""Generate the topic registry and the QoS table from the specification text.

    python3 tools/gen_registry.py            # write artifacts/registry.json
    python3 tools/gen_registry.py --check    # fail if it differs from the spec at the pin

Four codebases hand-transcribed §3.3.3, and every copy drifted from the spec or
from the others (docs/NATIVE-SDK-CENSUS.md). The table is normative --- "writers
and readers SHALL set exactly these policies" --- so the native layer reads it
from the spec's own text at the pinned tag, and a gate fails if a regeneration
disagrees with what is vendored.

Three tables and one kind of sentence, all from `sections/v1.8/02-idl-profiles.md`
at the pin:

* §3.3.3, **QoS Profiles**: each profile's reliability, durability, history and
  deadline. Ordering is recorded but sets no policy: "Ordered" in every row is
  DDS's per-instance default.
* §3.3.2, **Typed Topics Registry**: each type's IDL struct, the profile its row
  names (or `null`), any topic template the row gives, and any QoS the row
  spells out instead of naming a profile.
* **Well-Known Discovery Topics** with **QoS defaults for discovery topics**.
* §2's **profile topic templates**: "`X` is registered as type `x` in §3.3.2 and
  SHOULD be advertised on the topic `spatialdds/<scene>/...`". §3.3.1 makes such
  a template "normative as specified where [it is] defined" and lets it "use
  additional path segments", so `spatialdds/<scene>/plan/<agent_id>/trajectory/v1`
  is a name the native layer must open, though it is six segments. Recorded
  with the sentence's own keyword (SHOULD), beside the type's row. Until 0.7.3
  these were not read at all, and the native layer refused the plan template
  (S15, found by SpatialDDS-demo).

Nothing is inferred. A row that names no profile records `profile: null` and
whatever policies it does spell; the native layer refuses to pick a profile for
it (docs/NATIVE-SDK-CENSUS.md, ruling 2). A row this parser cannot read is an
error rather than a skipped row, because a silent skip would be a fifth
transcription that is merely shorter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "lib" / "spatialdds" / "spatialdds" / "artifacts"
OUT = ARTIFACTS / "registry.json"
SPEC = ROOT.parent / "SpatialDDS-spec"
PIN = "v1.8"
SOURCE = "sections/v1.8/02-idl-profiles.md"

PROFILE_ROW = re.compile(
    r"^\| `(?P<name>[A-Z_]+)` \| (?P<rel>Reliable|Best-effort) \| (?P<ord>Ordered) "
    r"\| (?P<dur>Transient-local|Volatile) \| (?P<hist>KeepLast\((?P<depth>\d+)\)|KeepAll) "
    r"\| (?P<deadline>—|(?P<ms>\d+) ms) \| (?P<use>[^|]+) \|$")
TYPE_ROW = re.compile(r"^\| `(?P<type>[a-z0-9_]+)` \| (?P<payload>[^|]+) \| (?P<notes>.+) \|$")
IDL_TYPE = re.compile(r"`((?:spatial::)?[a-z_]+(?:::[a-z_]+)*::[A-Z]\w+)`")
NAMED_QOS = re.compile(r"QoS `([A-Z_]+)`")
TEMPLATE = re.compile(r"\bon `(spatialdds/[^`]+)`")
PROFILE_TEMPLATE = re.compile(
    r"is registered as type `(?P<type>[a-z0-9_]+)` in §3\.3\.2 and "
    r"(?P<level>MUST|SHALL|SHOULD|MAY) be advertised on the topic "
    r"`(?P<topic>spatialdds/[^`]+)`")
WELL_KNOWN_ROW = re.compile(r"^\| `(?P<type>[A-Z]\w+)` \| `(?P<topic>spatialdds/discovery/[a-z_]+/v\d+)` \|$")
DISCO_QOS_ROW = re.compile(
    r"^\| `(?P<topic>[a-z_]+)` \| (?P<rel>RELIABLE|BEST_EFFORT) \| (?P<dur>TRANSIENT_LOCAL|VOLATILE) "
    r"\| (?P<hist>KEEP_LAST\((?P<depth>\d+)\)(?: per key)?|KEEP_ALL) \|$")


def spec_text(ref: str = PIN) -> str:
    """The section at the pinned tag, read from git rather than a working tree."""
    if not (SPEC / ".git").exists():
        raise SystemExit(f"needs a SpatialDDS-spec checkout at {SPEC}")
    r = subprocess.run(["git", "-C", str(SPEC), "show", f"{ref}:{SOURCE}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git show {ref}:{SOURCE} failed: {r.stderr.strip()}")
    return r.stdout


def _section(text: str, heading: str, until: str) -> list[str]:
    start = text.index(heading)
    end = text.index(until, start + len(heading))
    return text[start:end].splitlines()


def _spelled(notes: str) -> dict:
    """The policies a registry row spells out in prose, and only those."""
    q: dict = {}
    if re.search(r"\bRELIABLE\b", notes):
        q["reliability"] = "RELIABLE"
    elif re.search(r"\bBEST_EFFORT\b", notes):
        q["reliability"] = "BEST_EFFORT"
    if re.search(r"\bTRANSIENT_LOCAL\b", notes):
        q["durability"] = "TRANSIENT_LOCAL"
    elif re.search(r"\bVOLATILE\b", notes):
        q["durability"] = "VOLATILE"
    m = re.search(r"\bKEEP_LAST\((\d+)\)", notes)
    if m:
        q["history"] = {"kind": "KEEP_LAST", "depth": int(m.group(1))}
    elif re.search(r"\bKEEP_ALL\b", notes):
        q["history"] = {"kind": "KEEP_ALL"}
    return q


def parse(text: str) -> dict:
    profiles = {}
    for line in _section(text, "#### **3.3.3 QoS Profiles**", "###### Notes"):
        if not line.startswith("| `"):
            continue
        m = PROFILE_ROW.match(line)
        if not m:
            raise SystemExit(f"§3.3.3: cannot read row: {line}")
        profiles[m["name"]] = {
            "reliability": "RELIABLE" if m["rel"] == "Reliable" else "BEST_EFFORT",
            "durability": "TRANSIENT_LOCAL" if m["dur"] == "Transient-local" else "VOLATILE",
            "history": ({"kind": "KEEP_LAST", "depth": int(m["depth"])} if m["depth"]
                        else {"kind": "KEEP_ALL"}),
            "deadline_ms": int(m["ms"]) if m["ms"] else None,
            "ordering": m["ord"],
            "use": m["use"].strip(),
        }

    types = {}
    lines = _section(text, "#### **3.3.2 Typed Topics Registry**",
                     "##### **Informative Example Registrations**")
    for line in lines:
        if not line.startswith("| `"):
            continue
        m = TYPE_ROW.match(line)
        if not m:
            raise SystemExit(f"§3.3.2: cannot read row: {line[:120]}")
        notes = m["notes"]
        idl = IDL_TYPE.findall(notes)
        if not idl:
            raise SystemExit(f"§3.3.2: {m['type']} names no IDL struct")
        named = NAMED_QOS.findall(notes)
        if len(set(named)) > 1:
            raise SystemExit(f"§3.3.2: {m['type']} names two profiles: {named}")
        profile = named[0] if named else None
        if profile is not None and profile not in profiles:
            raise SystemExit(f"§3.3.2: {m['type']} names {profile}, not in §3.3.3")
        fqn = idl[0] if idl[0].startswith("spatial::") else "spatial::" + idl[0]
        template = TEMPLATE.search(notes)
        types[m["type"]] = {
            "idl": fqn,
            "profile": profile,
            "topic_template": template.group(1) if template else None,
            # Only for rows that name no profile: what they spell instead.
            "spelled_qos": _spelled(notes) if profile is None else {},
            "provisional": "(provisional)" in line,
        }

    for t in types.values():
        t["profile_template"] = None
    for m in PROFILE_TEMPLATE.finditer(text):
        row = types.get(m["type"])
        if row is None:
            raise SystemExit(f"§2 gives {m['type']} a topic template, but "
                             f"§3.3.2 has no such row")
        if row["profile_template"] is not None:
            raise SystemExit(f"§2 gives {m['type']} two topic templates")
        row["profile_template"] = {"topic": m["topic"], "level": m["level"]}
    # Every templated name the section mentions is one of these or a §3.3.2
    # row's, so a template written in other words is an error, not a skip.
    read = ({t["topic_template"] for t in types.values()} |
            {t["profile_template"]["topic"] for t in types.values()
             if t["profile_template"]})
    for name in set(re.findall(r"`(spatialdds/<scene>/[^`]+)`", text)) - read:
        raise SystemExit(f"the section names the template {name}, and this "
                         f"parser read no rule for it")

    well_known = {}
    for line in _section(text, "#### **Well-Known Discovery Topics (Normative)**",
                         "**QoS defaults for discovery topics**"):
        m = WELL_KNOWN_ROW.match(line)
        if m:
            well_known[m["type"]] = {"idl": f"spatial::disco::{m['type']}",
                                     "topic": m["topic"]}
    disco_qos = {}
    for line in _section(text, "**QoS defaults for discovery topics**",
                         "**CoverageResponse reply topic QoS"):
        if not line.startswith("| `"):
            continue
        m = DISCO_QOS_ROW.match(line)
        if not m:
            raise SystemExit(f"discovery QoS: cannot read row: {line}")
        disco_qos[m["topic"]] = {
            "reliability": m["rel"], "durability": m["dur"],
            "history": ({"kind": "KEEP_LAST", "depth": int(m["depth"])} if m["depth"]
                        else {"kind": "KEEP_ALL"}),
            "deadline_ms": None,
        }
    for t, row in well_known.items():
        seg = row["topic"].split("/")[2]
        if seg not in disco_qos:
            raise SystemExit(f"well-known {t}: no QoS row for {seg!r}")
        row["qos"] = disco_qos[seg]
    if not (profiles and types and well_known):
        raise SystemExit("a table came back empty; the section layout changed")
    return {"profiles": profiles, "types": types, "well_known": well_known}


def build(ref: str = PIN) -> dict:
    text = spec_text(ref)
    commit = subprocess.run(["git", "-C", str(SPEC), "rev-parse", f"{ref}^{{commit}}"],
                            capture_output=True, text=True, check=True).stdout.strip()
    doc = parse(text)
    return {
        "generated_by": "tools/gen_registry.py",
        "spec": {"tag": ref, "commit": commit, "source": SOURCE,
                 "source_sha256": hashlib.sha256(text.encode()).hexdigest()},
        **doc,
    }


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    fresh = dump(build())
    if args.check:
        have = OUT.read_text() if OUT.exists() else ""
        if have != fresh:
            print(f"{OUT.relative_to(ROOT)} differs from the spec at {PIN}; "
                  "regenerate with python3 tools/gen_registry.py")
            return 1
        doc = json.loads(fresh)
        print(f"registry matches {PIN}: {len(doc['profiles'])} profiles, "
              f"{len(doc['types'])} types, {len(doc['well_known'])} well-known topics")
        return 0
    OUT.write_text(fresh)
    doc = json.loads(fresh)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(doc['profiles'])} profiles, "
          f"{len(doc['types'])} types, {len(doc['well_known'])} well-known topics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
