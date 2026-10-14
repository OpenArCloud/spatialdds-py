#!/usr/bin/env python3
"""The README's quickstart, executed as text a reader pastes.

The first version of this gate stripped the `$ ` prompt from each fence and
expanded `$BASE` itself before running the command. It passed every time while
the page was unrunnable: GitHub's copy button yields the fence verbatim --
prompt, command and expected output together -- and pasting that into a shell
gives `command not found: $`, an empty pipe, and then the output lines executed
as commands. The gate normalised its input into something that worked and then
reported that it worked. Both named defect classes at once, in the gate written
to catch one of them.

So this gate does no normalising. Rules, in order of importance:

1. **A command fence is executed verbatim.** Whatever bytes the fence holds go
   to the shell unchanged. If a `$ ` prompt marker is in there, it fails here,
   exactly as it fails for a reader.
2. **No port substitution.** The serve line's own port is used, so the URLs in
   the documented output are the URLs a reader sees. If something already holds
   that port this gate FAILS and names the occupant, because a gate that cannot
   observe its subject must not report green.
3. **The environment comes from the page.** The export fence is prepended to
   each command fence, which is what a reader's shell carries after pasting it.
   Nothing else is injected.
4. Commands and their expected output live in **separate fences**, so that each
   is independently copyable. A marker introduces the command fence; the next
   `console` fence is its expected output.

`gates/examples.py` still runs the same examples against an ephemeral port and
may substitute hosts to do it. That is a different question -- does this command
work against an endpoint -- and this gate is the one that answers whether the
page can be followed.

Usage:  gates/quickstart.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import examples as ex                                         # noqa: E402
from harness import report                                   # noqa: E402

README = ROOT / "README.md"
# Every document whose commands a reader is meant to run. The walkthrough
# joins the front page here rather than getting a gate of its own: the rule is
# the same one, and a second gate would be a second place for it to drift.
DOCS = [README, ROOT / "docs" / "WALKTHROUGH.md"]
SHELL = "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh"


def fences(text: str) -> list[tuple[int, str, list[str], str]]:
    """Every fence: (line, tag, body, the marker preceding it or "")."""
    out, lines, marker = [], text.splitlines(), ""
    inf, tag, start, buf = False, "", 0, []
    for i, l in enumerate(lines, 1):
        m = ex.MARKER.match(l.strip())
        if m and not inf:
            marker = m.group(1)
            continue
        if not inf and l.startswith("```"):
            inf, tag, start, buf = True, l[3:].strip(), i, []
        elif inf and l.strip() == "```":
            out.append((start, tag, buf, marker))
            inf, marker = False, ""
        elif inf:
            buf.append(l)
    return out


def logical(lines: list[str], start: int) -> str:
    """One shell command, with backslash continuations joined.

    A serve line that wraps is still one command, and taking only the physical
    line leaves a trailing backslash the shell rejects.
    """
    out = lines[start]
    while out.rstrip().endswith("\\") and start + 1 < len(lines):
        start += 1
        out = out.rstrip()[:-1] + " " + lines[start].strip()
    return " ".join(out.split())


def listening(port: int) -> str:
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                       capture_output=True, text=True)
    rows = [l for l in r.stdout.splitlines()[1:] if l.strip()]
    return rows[0] if rows else ""


def main() -> int:
    """Every document whose commands are meant to be run, walked in order."""
    checks: list[tuple[str, bool, str]] = []
    for doc in DOCS:
        rc = walk(doc, checks)
        if rc is not None:
            return rc
    return report("quickstart", checks,
                  "every page can be pasted, not merely interpreted")


def walk(doc: Path, checks: list) -> int | None:
    name = doc.relative_to(ROOT).as_posix()
    blocks = fences(doc.read_text())
    plain = [b for b in blocks if b[1] == "sh" and not b[3]]
    assert len(plain) >= 2, "expected an install/serve fence and an export fence"
    serve_fence, export_fence = plain[0], plain[1]
    # The page may stand the endpoint up more than once -- the publish section
    # restarts it with the write surface on -- so the walk is a sequence of
    # phases in document order: an unmarked fence holding a serve line opens a
    # phase, and the marked fences after it belong to that phase. A reader
    # stops one gateway before starting the next, and so does this.
    phases: list[tuple[str, list]] = []
    for n, b in enumerate(blocks):
        if b[1] == "sh" and not b[3] and any("run_gateway.py" in l
                                             for l in b[2]):
            i = next(k for k, l in enumerate(b[2]) if "run_gateway.py" in l)
            phases.append((logical(b[2], i), []))
        elif b[3] and b[1] == "sh" and phases:
            nxt = blocks[n + 1] if n + 1 < len(blocks) else None
            want = nxt[2] if nxt and nxt[1] == "console" else None
            phases[-1][1].append((b[0], b[1], b[2], b[3], want))

    packages = [l.split()[2:] for l in serve_fence[2]
                if l.strip().startswith("pip install")]
    missing = [p for p in (packages[0] if packages else [])
               if __import__("importlib.util", fromlist=["util"])
               .find_spec(p) is None]
    if packages:
        checks.append((f"{name}: the install line's packages are importable "
                       f"({', '.join(packages[0])})",
                       not missing, f"not installed: {missing}"))

    si = next((k for k, l in enumerate(serve_fence[2])
               if "run_gateway.py" in l), None)
    serve = logical(serve_fence[2], si) if si is not None else ""
    m = re.search(r"--mcap\s+(\S+)", serve)
    port = int(re.search(r"--port\s+(\d+)", serve).group(1)) \
        if re.search(r"--port\s+(\d+)", serve) else 8787
    checks.append((f"{name}: the serve line names a recording", bool(m),
                   serve))
    if not m:
        return report("quickstart", checks, f"{name} has no serve line")
    served = (ROOT / m.group(1)).resolve()

    # Every marker on the page must introduce a runnable command fence. A
    # marker sitting on a `console` fence means the page still carries the
    # prompt-and-output shape this gate exists to reject, and finding zero
    # tiers to run must fail rather than pass on nothing -- which is exactly
    # what this gate did on its first run.
    # Each mode has one fence type that can hold a command alone: a shell tier
    # is ```sh, a console tier is ```js. A marker on a ```console fence means
    # the page still carries the prompt-and-output shape, where the copy button
    # yields a prompt and expected output along with the command.
    WANT = {"sh": "sh", "py": "sh", "py-fails": "sh", "js": "js",
            "js-ok": "js"}
    marked = [b for b in blocks if b[3]]
    misplaced = [f"{name}:{b[0]} is ```{b[1] or '(none)'}, "
                 f"mode {b[3].split()[1]} wants "
                 f"```{WANT.get(b[3].split()[1], '?')}"
                 for b in marked
                 if b[1] != WANT.get(b[3].split()[1])]
    checks.append((f"{name}: every marker sits on a fence holding its "
                   f"command alone "
                   f"({len(marked) - len(misplaced)} of {len(marked)})",
                   bool(marked) and not misplaced,
                   "; ".join(misplaced) or "no marked fence on the page"))
    runnable = [b for b in marked if b[1] == "sh"]
    wrong = [f"{name}:{b[0]} names {b[3].split()[0]!r}" for b in runnable
             if ex.RECORDINGS[b[3].split()[0]][0].resolve() != served]
    checks.append((f"{name}: every tier names the recording the serve line "
                   f"serves ({served.name}, {len(runnable)} tier(s))",
                   not wrong, "; ".join(wrong)))

    checks.append((f"{name}: stands the endpoint up {len(phases)} time(s), "
                   f"and every marked shell tier belongs to one "
                   f"({sum(len(c) for _s, c in phases)} of {len(runnable)})",
                   bool(phases)
                   and sum(len(c) for _s, c in phases) == len(runnable),
                   f"{[(s_.split()[-1], len(c)) for s_, c in phases]}"))

    busy = listening(port)
    if busy:
        checks.append((f"{name}: port {port}, as the serve line writes it, "
                       f"is free", False, f"held by: {busy[:110]}"))
        return report("quickstart", checks,
                      f"cannot observe {name} on its own port")

    env_text = "\n".join(export_fence[2])
    for serve_line, cmd_blocks in phases:
        rc = await_phase(name, serve_line, cmd_blocks, env_text, port, checks)
        if rc is not None:
            return rc
    return None


def await_phase(name: str, serve: str, cmd_blocks: list, env_text: str,
                port: int, checks: list) -> int | None:
    child = subprocess.Popen([sys.executable] + serve.split()[1:], cwd=ROOT,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    try:
        owner = ""
        for _ in range(300):
            if child.poll() is not None:
                break
            owner = listening(port)
            if owner and f" {child.pid} " in f" {owner.split()[1]} ":
                break
            time.sleep(0.1)
        if child.poll() is not None:
            out = (child.stdout.read() if child.stdout else "")[-300:]
            checks.append((f"{name}: the serve line starts an endpoint",
                           False,
                           f"exited {child.returncode}: {out.strip()}"))
            return report("quickstart", checks,
                          f"{name}'s serve line did not serve")
        checks.append((f"{name}: the serve line's own process is listening on "
                       f"{port}",
                       bool(owner) and owner.split()[1] == str(child.pid),
                       f"{owner[:80]!r} vs started pid {child.pid}"))

        for line, _tag, body, marker, want in cmd_blocks:
            if want is None:
                checks.append((f"{name}:{line}: its output fence follows it",
                               False, "no ```console fence after the command"))
                continue
            script = f"{env_text}\n" + "\n".join(body)
            r = subprocess.run([SHELL, "-c", script], cwd=ROOT,
                               capture_output=True, text=True, timeout=180)
            have = [l.rstrip() for l in r.stdout.splitlines()]
            exp = [l.rstrip() for l in want]
            ok = have == exp
            detail = ""
            if not ok:
                bad = next((f"line {i + 1}: want {w!r}, got {g!r}"
                            for i, (w, g) in enumerate(zip(exp, have))
                            if w != g), f"{len(exp)} want, {len(have)} got")
                err = (r.stderr.strip().splitlines() or [""])[0][:100]
                detail = bad + (f"; stderr: {err}" if err else "")
            checks.append((f"{name}:{line} pasted verbatim", ok, detail))
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
    return None


if __name__ == "__main__":
    sys.exit(main())
