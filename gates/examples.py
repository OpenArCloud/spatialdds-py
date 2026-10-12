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
# name -> (recording, the base the endpoint believes it is reached at).
# `authority` is the fountain recording again, served as the DNS name its
# manifests' URIs resolve against, which is the only configuration in which
# §7.5.2 resolution can answer. Separate from `fountain` so that every other
# example keeps following absolute advertised URLs unaltered.
RECORDINGS = {
    "retail": (ROOT / "samples" / "queuing-retail-sample.mcap", None),
    "fountain": (ROOT / "samples" / "fountain-anchors.mcap", None),
    "authority": (ROOT / "samples" / "fountain-anchors.mcap",
                  "https://demo.spatialdds.org"),
}
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
        if self.mode == "py-fails":
            return f"{self.src}:{self.line} [{self.rec}] {self.target} refuses"
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
        assert mode in ("py", "py-fails", "sh", "js", "js-ok"), \
            f"{where} mode {mode}"
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
        elif mode == "sh":
            # A command fence holding the command alone, then its output
            # fence. One fence carrying `$ ` prompts and output together is
            # what GitHub's copy button turns into an unrunnable paste, so the
            # grammar no longer admits it.
            b.program, j = fence(lines, i, "sh")
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
    err = ""
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
        if block.mode == "py-fails":
            # The documented refusal. A newcomer's first failure is text they
            # read, so it is pinned like any other output -- and it must be a
            # refusal: a zero exit here would mean the example worked against
            # a recording the page says it cannot.
            r = subprocess.run([sys.executable, block.target], cwd=ROOT,
                               env=env, capture_output=True, text=True,
                               timeout=120)
            if r.returncode == 0:
                return False, "exited 0; the page documents a refusal"
            if "Traceback" in r.stderr:
                return False, "refused with a traceback, not a sentence"
            want = [l.replace("$BASE", base).rstrip()
                    for l in block.expected]
            # The tail of stderr: an interpreter may prepend warnings a reader
            # sees or does not (a urllib3 version notice, say), and pinning
            # those would make the documented message depend on the
            # environment it was captured in. The refusal is the last thing
            # printed, which is what a reader reads.
            have = [l.rstrip() for l in
                    r.stderr.strip().splitlines()][-len(want):]
            return (want == have, "" if want == have else
                    f"want {want!r}, got {have!r}")
        if block.mode == "py":
            r = subprocess.run([sys.executable, block.target], cwd=ROOT,
                               env=env, capture_output=True, text=True,
                               timeout=120)
            got, err = r.stdout, r.stderr
        elif block.mode == "sh":
            # Verbatim: the fence's bytes go to the shell unchanged. `$BASE`
            # is resolved by the shell from the environment, the way a reader's
            # shell resolves it after the export block -- not by this gate
            # rewriting the command first.
            r = subprocess.run(["sh", "-c", "\n".join(block.program)],
                               cwd=ROOT, env=env, capture_output=True,
                               text=True, timeout=120)
            got, err = r.stdout, r.stderr
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

    if not got.strip() and err and err.strip():
        # An example that printed nothing failed for a reason, and the reason
        # is in stderr. Reporting only "0 lines printed" sends a reader back
        # to the terminal to find out what this already knows.
        last = [l for l in err.strip().splitlines() if l.strip()][-1]
        return False, f"printed nothing; stderr ends: {last.strip()[:140]}"

    # The documented output names the port the README's serve line uses. This
    # gate serves on an ephemeral one, so the host is rewritten on the
    # *expected* side only; the command itself is never touched.
    want = [l.replace("$BASE", base).replace("http://127.0.0.1:8787", base)
            .rstrip() for l in block.expected]
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

    for rec, (mcap, advertise) in RECORDINGS.items():
        mine = [b for b in blocks if b.rec == rec]
        if not mine:
            continue
        gw = await Gateway(state(mcap), advertise=advertise).start()
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

    # The recording-mismatch sweep. An example run against a recording it did
    # not ask for must refuse in a sentence, never in a traceback: that is the
    # defect that put StopIteration in a newcomer's first thirty seconds, and
    # fixing the four scripts without asserting the property leaves the fifth
    # to rediscover it.
    declared: dict[str, set[str]] = {}
    for b in blocks:
        if b.mode == "py":
            declared.setdefault(b.target, set()).add(b.rec)
        if b.mode == "py-fails":
            # A documented refusal is a declaration too: that configuration is
            # covered by its own pinned message, so the sweep need not re-run
            # it blind.
            declared.setdefault(b.target, set()).add(b.rec)
    # Swept over the configurations that advertise the address they are
    # reached at. `authority` advertises `demo.spatialdds.org` while listening
    # on loopback, so an example that correctly follows an absolute advertised
    # URL fails there on DNS -- a property of the harness, not of the example,
    # and not a situation the public endpoint puts a reader in. The exclusion
    # is asserted rather than assumed: if that configuration ever advertises
    # its own base, it rejoins the sweep.
    swept = [r for r, (_m, adv) in RECORDINGS.items() if adv is None]
    excluded = {r: adv for r, (_m, adv) in RECORDINGS.items() if adv}
    checks.append((f"the mismatch sweep covers every self-advertising "
                   f"configuration ({', '.join(swept)}); excluded "
                   f"{sorted(excluded)} advertise elsewhere",
                   all(a and not a.startswith("http://127.0.0.1")
                       for a in excluded.values()),
                   f"{excluded}"))
    for target, decl in sorted(declared.items()):
        for rec in swept:
            if rec in decl:
                continue
            mcap, advertise = RECORDINGS[rec]
            gw = await Gateway(state(mcap), advertise=advertise).start()
            try:
                r = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(
                        [sys.executable, target], cwd=ROOT, timeout=120,
                        capture_output=True, text=True,
                        env=dict(os.environ, SPATIALDDS_ENDPOINT=gw.base,
                                 PYTHONPATH=str(ROOT))))
            finally:
                await gw.stop()
            if r.returncode == 0:
                continue          # it works there too; undeclared, not broken
            checks.append((f"{target} against [{rec}]: refuses in a sentence, "
                           f"not a traceback",
                           "Traceback" not in r.stderr,
                           (r.stderr.strip().splitlines() or ["no output"])[-1]
                           [:120]))

    scripts = sorted(p.relative_to(ROOT).as_posix()
                     for p in (ROOT / "examples").rglob("*.py"))
    covered = {b.target for b in blocks if b.mode in ("py", "py-fails")}
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
