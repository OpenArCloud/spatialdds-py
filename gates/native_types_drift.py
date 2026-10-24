#!/usr/bin/env python3
"""The checked-in bindings are what the generator writes from the vendored IDL now.

`tools/generate_types.py --check`, run where idlc is: the `cyclonedds-python`
image. It regenerates into a temporary directory and diffs. A stale tree is a
failure, and a different idlc version from the one the headers record is
reported as its own outcome (exit 2), because two toolchains differ in layout
regardless of staleness.

`gates/run_all.py` skips this, with the reason, where Docker or the image is
absent. It does not pass in that case: the skip is a stated absence.

Usage:  gates/native_types_drift.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

from harness import report                                    # noqa: E402

IMAGE = "cyclonedds-python"


def main() -> int:
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{ROOT}:/repo", "-w", "/repo", IMAGE,
         "python3", "tools/generate_types.py", "--check"],
        capture_output=True, text=True, timeout=600)
    out = (r.stdout + r.stderr).strip()
    checks = [
        ("the generator ran in the idlc image", r.returncode in (0, 1, 2),
         out[-300:]),
        ("the same toolchain as the checked-in headers", r.returncode != 2,
         out[-300:]),
        ("the checked-in tree is what the generator writes now",
         r.returncode == 0 and "up to date" in out, out[-600:]),
    ]
    return report("native bindings drift", checks,
                  "the bindings are regenerated from the pin without a diff")


if __name__ == "__main__":
    sys.exit(main())
