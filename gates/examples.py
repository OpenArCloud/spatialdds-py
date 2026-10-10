#!/usr/bin/env python3
"""Every example runs, and prints what it is documented to print.

An example is a claim about the endpoint in executable form, and a stale one is
worse than none: it teaches a reader something untrue and then wastes their
afternoon proving it. So the documented output is not transcribed prose here --
it is the expected value, parsed out of the markdown and diffed against what
the example actually prints, against a locally served recording.

The markdown carries machine-readable markers:

    <!-- gate: retail py examples/zones.py -->   the next ```console fence is
                                                 that script's expected stdout
    <!-- gate: fountain sh -->                   the next ```console fence is
                                                 shell lines prefixed `$ ` and
                                                 their expected output
    <!-- gate: fountain js -->                   the next ```js fence is a
                                                 program; the ```console fence
                                                 after it is its expected output

`retail` and `fountain` name which recording to serve. `$BASE` in an expected
output stands for the endpoint it was served from, which is the one thing that
legitimately differs between a reader's run and this gate's.

The last check is the one that keeps the gate honest: every marker found in
every example file must have been executed. If `node` is missing the browser
snippets cannot be verified, and a gate that cannot verify its subject reports
FAIL rather than PASS -- `gates/run_all.py` is where a missing interpreter
turns into a stated SKIPPED instead.

Usage:  gates/examples.py
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

from harness import Gateway, report, state                     # noqa: E402

# The front page is gated too. Its three tiers are the most-read claims in the
# repository and the ones a reader tries first, so they get the same treatment
# as the examples they lead to. The markers are HTML comments and render as
# nothing.
FILES = ["README.md", "examples/README.md", "examples/curl.md",
         "examples/browser.md"]
RECORDINGS = {"retail": ROOT / "samples" / "queuing-retail-sample.mcap",
              "fountain": ROOT / "samples" / "fountain-anchors.mcap"}
# Anchored, because `examples/README.md` documents this grammar by quoting it
# inline, and a parser that reads its own documentation as input is the
# self-observing check this repository has named five times. A marker counts
# only when it is the entire line; a mention inside prose or backticks is
# counted separately and reported, so the exclusion is visible rather than
# silent.
MARKER = re.compile(r"^<!--\s*gate:\s*(.+?)\s*-->$")
MENTION = re.compile(r"<!--\s*gate:\s*.+?\s*-->")
LOCAL = "127.0.0.1:8787"          # the URL the browser snippets carry inline
DEMO = "https://demo.spatialdds.org"   # the placeholder the front page carries
INLINE: list[str] = []            # markers quoted in prose, deliberately unread


class Block:
    def __init__(self, src: str, line: int, rec: str, mode: str,
                 target: str) -> None:
        self.src, self.line, self.rec = src, line, rec
        self.mode, self.target = mode, target
        self.program: list[str] = []      # js only
        self.expected: list[str] = []

    @property
    def label(self) -> str:
        what = self.target or {"sh": "shell one-liners",
                               "js": "console snippet",
                               "js-ok": "console snippet runs clean"}[
                                   self.mode]
        return f"{self.src}:{self.line} [{self.rec}] {what}"


def fence(lines: list[str], i: int, tag: str) -> tuple[list[str], int]:
    """The body of the next ```<tag> fence at or after line i."""
    while i < len(lines) and lines[i].strip() != "```" + tag:
        i += 1
    if i >= len(lines):
        raise AssertionError(f"no ```{tag} fence found")
    body, i = [], i + 1
    while i < len(lines) and lines[i].strip() != "```":
        body.append(lines[i])
        i += 1
    return body, i


def parse(path: Path) -> list[Block]:
    lines = path.read_text().splitlines()
    out: list[Block] = []
    for i, line in enumerate(lines):
        m = MARKER.match(line.strip())
        if not m:
            if MENTION.search(line):
                INLINE.append(f"{path.relative_to(ROOT).as_posix()}:{i+1}")
            continue
        parts = m.group(1).split()
        rec, mode = parts[0], parts[1]
        target = parts[2] if len(parts) > 2 else ""
        where = f"{path.relative_to(ROOT).as_posix()}:{i+1}"
        assert rec in RECORDINGS, f"{where} unknown recording {rec}"
        assert mode in ("py", "sh", "js", "js-ok"), f"{where} mode {mode}"
        b = Block(path.relative_to(ROOT).as_posix(), i + 1, rec, mode, target)
        if mode == "js-ok":
            # Run it and require it to work, without pinning its output: the
            # front page shows this snippet without its output, because a
            # devtools table is not a thing a README can print. A snippet that
            # throws still fails here.
            b.program, _ = fence(lines, i, "js")
        elif mode == "js":
            b.program, j = fence(lines, i, "js")
            b.expected, _ = fence(lines, j, "console")
        else:
            b.expected, _ = fence(lines, i, "console")
        out.append(b)
    return out


def commands(expected: list[str]) -> tuple[list[str], list[str]]:
    """Split a shell fence into its `$ `-prefixed commands and its output."""
    cmds, out, i = [], [], 0
    while i < len(expected):
        line = expected[i]
        if line.startswith("$ "):
            acc = line[2:]
            while acc.rstrip().endswith("\\"):
                i += 1
                acc = acc.rstrip()[:-1] + expected[i]
            cmds.append(acc)
        else:
            out.append(line)
        i += 1
    return cmds, out


def run(block: Block, base: str) -> tuple[bool, str]:
    env = dict(os.environ, SPATIALDDS_ENDPOINT=base, BASE=base,
               PYTHONPATH=str(ROOT))
    try:
        if block.mode == "js-ok":
            host = base.split("//", 1)[1]
            prog = ("\n".join(block.program).replace(DEMO, base)
                    .replace(LOCAL, host))
            with tempfile.NamedTemporaryFile("w", suffix=".mjs",
                                             delete=False) as fh:
                fh.write(prog)
                tmp = fh.name
            try:
                r = subprocess.run(["node", tmp], cwd=ROOT, env=env,
                                   capture_output=True, text=True,
                                   timeout=120)
            finally:
                os.unlink(tmp)
            if r.returncode != 0:
                # node's last stderr line is its own version banner; the
                # useful line is the first one naming the error.
                err = r.stderr.strip().splitlines()
                pick = next((l.strip() for l in err if "Error" in l),
                            err[0].strip() if err else "exited non-zero")
                return False, pick[:160]
            if not r.stdout.strip():
                return False, "ran without error and printed nothing"
            return True, ""
        if block.mode == "py":
            got = subprocess.run([sys.executable, block.target], cwd=ROOT,
                                 env=env, capture_output=True, text=True,
                                 timeout=120).stdout
        elif block.mode == "sh":
            cmds, _ = commands(block.expected)
            got = "".join(subprocess.run(["sh", "-c", c], cwd=ROOT, env=env,
                                         capture_output=True, text=True,
                                         timeout=120).stdout for c in cmds)
        else:
            host = base.split("//", 1)[1]
            prog = ("\n".join(block.program).replace(DEMO, base)
                    .replace(LOCAL, host))
            with tempfile.NamedTemporaryFile("w", suffix=".mjs",
                                             delete=False) as fh:
                fh.write(prog)
                tmp = fh.name
            try:
                got = subprocess.run(["node", tmp], cwd=ROOT, env=env,
                                     capture_output=True, text=True,
                                     timeout=120).stdout
            finally:
                os.unlink(tmp)
    except subprocess.TimeoutExpired:
        return False, "timed out"

    want = ([l for l in commands(block.expected)[1]] if block.mode == "sh"
            else block.expected)
    want = [l.replace("$BASE", base).rstrip() for l in want]
    have = [l.rstrip() for l in got.splitlines()]
    if want == have:
        return True, ""
    for n, (w, h) in enumerate(zip(want, have)):
        if w != h:
            return False, f"line {n+1}: want {w!r}, got {h!r}"
    return False, (f"{len(want)} documented line(s), {len(have)} printed; "
                   f"first extra: "
                   f"{(want[len(have):] or have[len(want):])[0]!r}")


async def main() -> int:
    blocks = [b for f in FILES for b in parse(ROOT / f)]
    checks: list[tuple[str, bool, str]] = []
    have_node = shutil.which("node") is not None
    ran = 0

    for rec, mcap in RECORDINGS.items():
        mine = [b for b in blocks if b.rec == rec]
        if not mine:
            continue
        gw = await Gateway(state(mcap)).start()
        try:
            for b in mine:
                if b.mode in ("js", "js-ok") and not have_node:
                    checks.append((b.label, False,
                                   "node is not installed, so this snippet "
                                   "was not verified"))
                    continue
                ok, detail = await asyncio.get_running_loop(
                ).run_in_executor(None, run, b, gw.base)
                ran += 1
                checks.append((b.label, ok, detail))
        finally:
            await gw.stop()

    scripts = sorted(p.relative_to(ROOT).as_posix()
                     for p in (ROOT / "examples").rglob("*.py"))
    covered = {b.target for b in blocks if b.mode == "py"}
    checks.append((f"every script under examples/ has a documented output "
                   f"({len(covered)} of {len(scripts)})",
                   set(scripts) == covered,
                   f"undocumented: {sorted(set(scripts) - covered)}"))
    checks.append((f"every marker in {len(FILES)} example file(s) was "
                   f"executed ({ran} of {len(blocks)})", ran == len(blocks),
                   "an example was documented but not run"))
    # Not a pass/fail: the count is here so that a marker silently ignored
    # because it sits inside prose is visible in the gate's own output.
    print(f"  note the grammar is documented by quoting it; "
          f"{len(INLINE)} inline mention(s) deliberately unread"
          + (f" ({', '.join(INLINE)})" if INLINE else ""))
    return report("examples", checks,
                  "every example runs and prints what it documents")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
