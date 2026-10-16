#!/usr/bin/env python3
"""Is a published wheel the one this tree builds?

    python3 tools/compare_wheel.py published.whl built.whl
    python3 tools/compare_wheel.py --self-test

A wheel is not bit-reproducible across build-tool versions: `WHEEL` records the
generator, `METADATA` records a metadata version, and `RECORD` records the
hashes of both. Comparing whole files therefore reports a difference for every
release built on a different machine, which trains a reader to ignore it. That
is the failure this exists to avoid.

So the comparison is in two parts, and only one of them is allowed to differ:

* **the payload** -- every file that is the package -- compared by SHA-256, and
  any difference at all is a different artifact;
* **`dist-info`** -- `METADATA`, `WHEEL`, `RECORD` -- diffed line by line and
  printed, so a one-line generator stamp reads as a one-line generator stamp
  and a changed long description reads as a changed long description.

Demonstrated on the real pair on 2026-10-16: PyPI's `spatialdds 0.2.0` against
a wheel built from public commit `5282a02`. All 139 payload files identical;
`METADATA` differed by one line of 294 (`Metadata-Version: 2.4` against `2.2`)
and `WHEEL` by one (`setuptools (84.0.0)` against `(75.8.0)`). That is what a
traceable release looks like, and anything else is a 0.x.y+1 from the pushed
tree rather than an explanation.

Exit status is 0 when the payload matches, 1 when it does not.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
import tempfile
import zipfile
from pathlib import Path

DIST_INFO = ("METADATA", "WHEEL", "RECORD")


def digests(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}


def is_dist_info(name: str) -> bool:
    return name.split("/")[-1] in DIST_INFO and ".dist-info/" in name


def compare(a: Path, b: Path, show: bool = True) -> tuple[bool, list[str]]:
    """`(payload matches, notes)`. Notes are for a person to read."""
    da, db = digests(a), digests(b)
    notes: list[str] = []
    only_a = sorted(set(da) - set(db))
    only_b = sorted(set(db) - set(da))
    differ = sorted(n for n in set(da) & set(db) if da[n] != db[n])

    payload_bad = ([n for n in only_a if not is_dist_info(n)]
                   + [n for n in only_b if not is_dist_info(n)]
                   + [n for n in differ if not is_dist_info(n)])
    n_payload = len([n for n in da if not is_dist_info(n)])
    notes.append(f"{len(da)} entries in {a.name}, {len(db)} in {b.name}")
    notes.append(f"payload: {n_payload} file(s), "
                 f"{len(payload_bad)} differing")
    for n in payload_bad:
        where = ("only in " + a.name if n in only_a else
                 "only in " + b.name if n in only_b else "content differs")
        notes.append(f"  DIFFERENT  {n}  ({where})")

    for n in sorted(n for n in differ if is_dist_info(n)):
        with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
            la = za.read(n).decode(errors="replace").splitlines()
            lb = zb.read(n).decode(errors="replace").splitlines()
        d = [l for l in difflib.unified_diff(la, lb, a.name, b.name,
                                             lineterm="", n=0)
             if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        notes.append(f"  dist-info  {n.split('/')[-1]}: "
                     f"{len(d)} line(s) differ of {len(la)}")
        for l in d[:8]:
            notes.append(f"    {l}")
    if show:
        for l in notes:
            print(l)
        print("\nPAYLOAD IDENTICAL" if not payload_bad else
              "\nPAYLOAD DIFFERS: this is a different artifact")
    return not payload_bad, notes


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return path


def self_test() -> int:
    """Exercise the three verdicts without needing a wheel or a network.

    Here because a release-time tool that has never run is the thing that
    fails the one time it is reached for.
    """
    base = {
        "spatialdds/__init__.py": b"__version__ = '0.2.0'\n",
        "spatialdds/artifacts/serializer_tables.json": b"{}\n",
        "spatialdds-0.2.0.dist-info/METADATA": b"Metadata-Version: 2.2\nName: x\n",
        "spatialdds-0.2.0.dist-info/WHEEL": b"Generator: setuptools (75.8.0)\n",
        "spatialdds-0.2.0.dist-info/RECORD": b"spatialdds/__init__.py,sha256=a\n",
    }
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        a = _zip(d / "a.whl", base)
        same = _zip(d / "same.whl", dict(base))
        ok, _ = compare(a, same, show=False)
        checks.append(("two identical wheels: payload matches", ok))

        stamp = dict(base)
        stamp["spatialdds-0.2.0.dist-info/WHEEL"] = \
            b"Generator: setuptools (84.0.0)\n"
        stamp["spatialdds-0.2.0.dist-info/METADATA"] = \
            b"Metadata-Version: 2.4\nName: x\n"
        ok, notes = compare(a, _zip(d / "stamp.whl", stamp), show=False)
        checks.append(("a different build tool: payload still matches", ok))
        checks.append(("...and both stamps are reported line by line",
                       any("Generator" in l for l in notes)
                       and any("Metadata-Version" in l for l in notes)))

        bad = dict(base)
        bad["spatialdds/__init__.py"] = b"__version__ = '0.2.0'\n# edited\n"
        ok, notes = compare(a, _zip(d / "bad.whl", bad), show=False)
        checks.append(("one changed payload byte: payload differs", not ok))
        checks.append(("...and the file is named",
                       any("spatialdds/__init__.py" in l for l in notes)))

        gone = {k: v for k, v in base.items() if "serializer_tables" not in k}
        ok, notes = compare(a, _zip(d / "gone.whl", gone), show=False)
        checks.append(("a missing payload file: payload differs", not ok))

    for label, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
    bad_n = len([c for c in checks if not c[1]])
    print(f"\n{'PASS' if not bad_n else 'FAIL'}, "
          f"{len(checks) - bad_n}/{len(checks)} checks")
    return 1 if bad_n else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wheels", nargs="*", type=Path,
                    help="two wheels: the published one and a fresh build")
    ap.add_argument("--self-test", action="store_true",
                    help="check this tool's own three verdicts, offline")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if len(a.wheels) != 2:
        raise SystemExit("give two wheels, or --self-test")
    for w in a.wheels:
        if not w.is_file():
            raise SystemExit(f"no such wheel: {w}")
    ok, _ = compare(*a.wheels)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
