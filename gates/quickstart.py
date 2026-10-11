#!/usr/bin/env python3
"""The README's quickstart, walked top to bottom as written.

`gates/examples.py` runs each example against the recording its own marker
names. That is right for examples/ and it is blind to the front page, where
the serve line and the tiers below it are one sequence a newcomer types in
order. A tier marked for a recording the serve line does not serve is green
individually and broken as a page -- which is exactly what happened:
`hello_spatial.py` asked for `spatial_zone`, the README served the fountain
recording, and a fresh clone got `StopIteration` on its first command.

So this gate executes the composition rather than the parts:

1. The install line's packages must be importable.
2. The serve line names one recording, and **every** marker on the front page
   must name a configuration serving that same recording. This is the
   structural check; the rest would pass again tomorrow without it.
3. The serve line is run as a subprocess -- the README's own command, not an
   in-process stand-in -- and readiness is the listening PID matching the
   child started, never a URL that merely answers.
4. Each tier runs in document order against that one endpoint, and its
   documented output must match.

One substitution: the port. The README says 8787 and a developer's machine
may already have something there, so the gate serves on a free port and
rewrites `$BASE` the same way `gates/examples.py` does. It is printed, and it
is the only difference between what this runs and what the page says.

Usage:  gates/quickstart.py
"""
from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import examples as ex                                         # noqa: E402
from harness import report                                    # noqa: E402

README = ROOT / "README.md"
# pip name -> import name, for the install line's own claim.
IMPORTS = {"requests": "requests", "websockets": "websockets",
           "aiohttp": "aiohttp", "mcap": "mcap"}


def quickstart_lines() -> tuple[list[str], str]:
    """The install line and the serve line, from the README's first sh fence."""
    body, _ = ex.fence(README.read_text().splitlines(), 0, "sh")
    serve = [l for l in body if "run_gateway.py" in l]
    install = [l for l in body if l.strip().startswith("pip install")]
    assert len(install) == 1, f"expected one install line, got {install}"
    assert len(serve) == 1, f"expected one serve line, got {serve}"
    return install[0].split()[2:], serve[0].strip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listening_pid(port: int) -> int | None:
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN",
                              "-t"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    pids = [int(x) for x in out.split()]
    return pids[0] if pids else None


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    packages, serve = quickstart_lines()

    missing = [p for p in packages
               if __import__("importlib.util", fromlist=["util"])
               .find_spec(IMPORTS.get(p, p)) is None]
    checks.append((f"the install line's packages are importable "
                   f"({', '.join(packages)})", not missing,
                   f"not installed: {missing}"))

    # ---- the structural check ------------------------------------------
    m = re.search(r"--mcap\s+(\S+)", serve)
    checks.append(("the serve line names a recording", bool(m), serve))
    if not m:
        return report("quickstart", checks, "no serve line to walk")
    served = (ROOT / m.group(1)).resolve()
    blocks = ex.parse(README)
    wrong = [f"{b.src}:{b.line} names {b.rec!r} "
             f"({ex.RECORDINGS[b.rec][0].name})" for b in blocks
             if ex.RECORDINGS[b.rec][0].resolve() != served]
    checks.append((f"every tier on the front page names the recording the "
                   f"serve line serves ({served.name}, {len(blocks)} tier(s))",
                   not wrong, "; ".join(wrong)))

    # ---- run the page --------------------------------------------------
    port = free_port()
    cmd = re.sub(r"--port\s+\d+", f"--port {port}", serve)
    print(f"  serve line: {serve}")
    print(f"  as run:     {cmd}   (port substituted, nothing else)\n")
    child = subprocess.Popen([sys.executable] + cmd.split()[1:], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        ready = owner = None
        for _ in range(300):
            if child.poll() is not None:
                break
            owner = listening_pid(port)
            if owner == child.pid:
                ready = True
                break
            time.sleep(0.1)
        if child.poll() is not None:
            out = (child.stdout.read() if child.stdout else "")[-300:]
            checks.append(("the serve line starts an endpoint", False,
                           f"exited {child.returncode}: {out.strip()}"))
            return report("quickstart", checks, "the serve line did not serve")
        checks.append((f"the serve line's own process is the one listening on "
                       f"{port}", ready is True,
                       f"listening pid {owner}, started {child.pid}"
                       if owner else "lsof unavailable: cannot confirm"))

        ran = 0
        for b in blocks:
            ok, detail = await asyncio.get_running_loop().run_in_executor(
                None, ex.run, b, base)
            ran += 1
            checks.append((f"tier at {b.src}:{b.line} ({b.mode})", ok, detail))
        checks.append((f"every tier on the page was executed ({ran} of "
                       f"{len(blocks)})", ran == len(blocks),
                       "a tier was documented but not run"))
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()

    return report("quickstart", checks,
                  "the front page works as a sequence, not just as parts")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
