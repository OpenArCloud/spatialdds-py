#!/usr/bin/env python3
"""Every extra the wheel declares is on the install block PyPI shows.

0.8.3 shipped `[mcp]` with a guide in the repository and no line on the
package's own page, so a reader on the index had no way to learn it existed.
This gate reads what ships rather than what the source says: it builds the
wheel from a copy of lib/spatialdds and takes the extras from its METADATA
(`Provides-Extra`) and the page from its long description, which is what
PyPI renders.

  * the long description is the README, byte for byte, so checking one is
    checking the other;
  * the page has an install block: a `sh` fence of `pip install` lines;
  * every declared extra is named in a `spatialdds[...]` requirement on one
    of those lines, and every extra a line names is declared.

The instrument is injected on every run: a second copy gets a planted extra
the README does not mention, and the gate requires that the plant reached
the built METADATA and that the check names it, and only it, as missing.

Usage:  gates/readme_extras.py [--lib DIR]
"""
from __future__ import annotations

import argparse
import email.parser
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"

from harness import report                                    # noqa: E402

PLANT = "planted-hidden"
REQ = re.compile(r"spatialdds\[([^\]]*)\]")


def norm(name: str) -> str:
    # PEP 685: extras compare normalized, as Provides-Extra writes them.
    return re.sub(r"[-_.]+", "-", name).lower()


def shipped(lib: Path, tmp: Path, plant: bool = False) -> tuple:
    """Build the wheel from a copy of `lib`; its extras and its page."""
    src = tmp / "src"
    shutil.copytree(lib, src, ignore=shutil.ignore_patterns(
        "build", "dist", "*.egg-info", "__pycache__"))
    if plant:
        pp = src / "pyproject.toml"
        text = pp.read_text()
        head = "[project.optional-dependencies]\n"
        pp.write_text(text.replace(head, head + f'{PLANT} = ["requests"]\n', 1))
    out = tmp / "wheel"
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                        "--no-build-isolation", "-q", "-w", str(out), str(src)],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"the wheel did not build:\n{r.stderr[-800:]}")
    whl = next(out.glob("*.whl"))
    with zipfile.ZipFile(whl) as z:
        meta = z.read(next(n for n in z.namelist()
                           if n.endswith(".dist-info/METADATA"))).decode()
    msg = email.parser.Parser().parsestr(meta)
    return sorted(msg.get_all("Provides-Extra") or []), msg.get_payload()


def install_block(page: str) -> list:
    """The `pip install` lines in the page's `sh` fences."""
    lines, tag = [], ""
    for line in page.splitlines():
        s = line.strip()
        if s.startswith("```"):
            tag = "" if tag else (s[3:].strip() or "-")
            continue
        if tag == "sh" and s.startswith("pip install"):
            lines.append(s)
    return lines


def undocumented(extras: list, block: list) -> tuple:
    named = {norm(x) for line in block for m in REQ.findall(line)
             for x in m.split(",") if x.strip()}
    declared = {norm(x) for x in extras}
    return sorted(declared - named), sorted(named - declared)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", type=Path, default=LIB,
                    help="the distribution to build (default lib/spatialdds)")
    lib = ap.parse_args().lib.resolve()
    checks: list = []
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "real").mkdir()
        extras, page = shipped(lib, Path(t) / "real")
        readme = (lib / "README.md").read_text()
        checks.append(("the wheel's long description is the README, so the "
                       "page PyPI renders is the one checked",
                       page.strip() == readme.strip(),
                       "the long description differs from README.md"))
        block = install_block(page)
        checks.append((f"the page has an install block "
                       f"({len(block)} pip install line(s))", bool(block),
                       "no sh fence with a pip install line"))
        missing, unknown = undocumented(extras, block)
        checks.append((f"every extra the METADATA declares is on it "
                       f"({', '.join(extras) or 'none declared'})",
                       not missing, f"undocumented: {missing}"))
        checks.append(("…and every extra it names is declared",
                       not unknown, f"not declared: {unknown}"))

        (Path(t) / "plant").mkdir()
        p_extras, p_page = shipped(lib, Path(t) / "plant", plant=True)
        landed = PLANT in p_extras
        checks.append((f"the plant landed: a copy declaring a `{PLANT}` "
                       f"extra builds a wheel whose METADATA declares it",
                       landed, f"Provides-Extra: {p_extras}"))
        p_missing, _ = undocumented(p_extras, install_block(p_page))
        checks.append(("…and this check names it as undocumented, beside "
                       "nothing the real copy does not already lack",
                       landed and p_missing == sorted(set(missing) | {PLANT}),
                       f"undocumented in the planted copy: {p_missing}"))
    return report("README extras", checks,
                  "no extra ships without its install line on the package's "
                  "own page")


if __name__ == "__main__":
    sys.exit(main())
