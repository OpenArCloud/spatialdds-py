#!/usr/bin/env python3
"""The README's native participant runs as printed, and a reader sees what it says.

Every python fence on the shipped page has an instrument (`gates/library.py`
refuses one without). A `<!-- native: name -->` block is a DDS program, so it
runs here rather than against an endpoint. The program and the reader the page
pairs with it run as separate processes on one domain. The page's
`domain=42` is replaced by a free one, the only substitution made. Each
process's output must equal the console fence under its block, line for line.

The two blocks are the acceptance's participant in a dozen lines on a bare
scene: an entity with an announce, pose updates, and a clean withdrawal on
exit. The reader's fence is what any participant on the domain sees.

Usage:  gates/native_readme.py
"""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"
README = LIB / "README.md"

from harness import report                                    # noqa: E402

MARKER = re.compile(r"<!--\s*native:\s*(\S+)\s*-->")


def blocks() -> dict:
    lines = README.read_text().splitlines()
    out = {}
    for i, line in enumerate(lines):
        m = MARKER.match(line.strip())
        if not m:
            continue
        j = i + 1
        while lines[j].strip() != "```python":
            j += 1
        k = j + 1
        while lines[k].strip() != "```":
            k += 1
        prog = lines[j + 1:k]
        c = k + 1
        while lines[c].strip() != "```console":
            c += 1
        e = c + 1
        while lines[e].strip() != "```":
            e += 1
        out[m.group(1)] = {"prog": "\n".join(prog), "want": lines[c + 1:e]}
    return out


def run(prog: str, domain: int, tmp: Path, name: str) -> subprocess.Popen:
    code = prog.replace("domain=42", f"domain={domain}")
    f = tmp / f"{name}.py"
    f.write_text(code + "\n")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(LIB),
           "PYTHONWARNINGS": "ignore"}
    return subprocess.Popen([sys.executable, str(f)], cwd=str(tmp), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def main() -> int:
    checks: list = []
    b = blocks()
    checks.append(("the page carries the participant and the reader as "
                   "native blocks, each with a console fence",
                   set(b) == {"participant", "reader"}, f"{sorted(b)}"))
    if set(b) != {"participant", "reader"}:
        return report("native README", checks, "the native blocks run as "
                      "printed")
    domain = random.randint(150, 229)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        reader = run(b["reader"]["prog"], domain, tmp, "reader")
        time.sleep(2.0)
        writer = run(b["participant"]["prog"], domain, tmp, "participant")
        w_out, w_err = writer.communicate(timeout=60)
        r_out, r_err = reader.communicate(timeout=60)
    w_lines = [x for x in w_out.splitlines() if x.strip()]
    r_lines = [x for x in r_out.splitlines() if x.strip()]
    checks.append(("the participant block runs to completion",
                   writer.returncode == 0, w_err[-400:]))
    checks.append(("…and prints exactly its console fence",
                   w_lines == b["participant"]["want"],
                   f"printed {w_lines}"))
    checks.append(("the reader block runs to completion",
                   reader.returncode == 0, r_err[-400:]))
    checks.append(("…and prints exactly its console fence: caught up, the "
                   "entity, its terminal sample, its dispose with the reason",
                   r_lines == b["reader"]["want"], f"printed {r_lines}"))
    nonblank = [x for x in b["participant"]["prog"].splitlines()
                if x.strip() and not x.strip().startswith("#")]
    checks.append((f"the participant is a dozen lines or so "
                   f"({len(nonblank)} non-blank)", len(nonblank) <= 16, ""))
    return report("native README", checks,
                  "the native blocks run as printed, and a reader sees what "
                  "the page says")


if __name__ == "__main__":
    sys.exit(main())
