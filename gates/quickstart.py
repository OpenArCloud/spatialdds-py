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
3. **Nothing is injected, including the environment.** An earlier version
   prepended the document's export fence to every command fence, and that is
   why it missed the next defect: a reader opens a second terminal, scrolls to
   the command they want, and pastes that block alone. Ten command blocks
   failed that way while this gate was green, because the gate's shell never
   forgot a variable. Each block now carries the exports it needs, and a block
   that does not fails here.
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

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import examples as ex                                         # noqa: E402
from harness import report                                   # noqa: E402

README = ROOT / "README.md"
#: The recording packaged inside the distribution, which `--demo` serves.
DEMO_MCAP = (ROOT / "lib" / "spatialdds" / "spatialdds" / "demo"
             / "fountain-anchors.mcap")


def digest_of(path: Path) -> str:
    """A recording's identity: its bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
# Every document whose commands a reader is meant to run. The walkthrough
# joins the front page here rather than getting a gate of its own: the rule is
# the same one, and a second gate would be a second place for it to drift.
DOCS = [README, ROOT / "docs" / "MYTOWN.md",
        ROOT / "docs" / "WALKTHROUGH.md",
        ROOT / "examples" / "curl.md"]
#: The two shapes a serve line comes in. `run_gateway.py` is the checkout's
#: shim; `-m spatialdds.serve` is what a reader who installed the package
#: types, and 0.2.0's front page leads with it.
SERVE_FORMS = ("run_gateway.py", "-m spatialdds.serve")
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


def local_installs(body: list[str]) -> tuple[list[str], list[str]]:
    """A pip fence's lines, split into local-path installs and the rest.

    A documented `pip install spatialdds` would reach PyPI, and a gate that
    reached PyPI would be testing a release instead of this commit -- it would
    also have installed the 0.0.1 placeholder for every day 0.2.0 was
    unreleased, which is the install-path hazard the from-source line beside
    it exists to close. So the local line is the one that runs, and a fence
    with a remote line and no local one beside it is a failure.
    """
    pips = [l.strip() for l in body if l.strip().startswith("pip install")]
    local = [l for l in pips
             if any(a.strip('"\'').startswith((".", "/"))
                    for a in l.split()[2:])]
    # Only this repository's own distribution counts as a release line. The
    # third-party prerequisites a page names -- requests, aiohttp, mcap --
    # have no from-source line and want none; they are checked for
    # importability instead, further down.
    remote = [l for l in pips if l not in local and "spatialdds" in l]
    return local, remote


def make_venv(tmp: Path) -> Path:
    """An environment with the checkout's package installed into it.

    The front page tells a reader to `pip install`, so the gate does that
    rather than assuming it: a virtual environment with system site-packages
    (for aiohttp, mcap, jsonschema and requests, which are the documented
    prerequisites) and the distribution in this checkout installed on top with
    `--no-deps --no-index`, so nothing is fetched.

    This is not the same thing as injecting an environment variable the
    document relies on -- finding 29's rule stands. It is reproducing the one
    step the document asks the reader to take before any of its commands work.
    """
    venv = tmp / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages",
                    str(venv)], check=True, capture_output=True)
    pip = venv / "bin" / "pip"
    # `--no-index` keeps the network out, and then `--no-build-isolation` is
    # required rather than optional: an isolated build would try to fetch its
    # own setuptools and find no index to fetch it from. The build backend
    # comes from the system site-packages this environment inherits.
    r = subprocess.run([str(pip), "install", "--quiet", "--no-deps",
                        "--no-index", "--no-build-isolation",
                        str(ROOT / "lib" / "spatialdds")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"could not install the checkout's package: "
                         f"{(r.stderr or r.stdout)[-400:]}")
    return venv


def listening(port: int) -> str:
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                       capture_output=True, text=True)
    rows = [l for l in r.stdout.splitlines()[1:] if l.strip()]
    return rows[0] if rows else ""


def main() -> int:
    """Every document whose commands are meant to be run, walked in order."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--refill", action="store_true",
                    help="rewrite each walked block's output fence with what "
                         "it printed. A block that printed nothing is "
                         "reported rather than written.")
    args = ap.parse_args()
    checks: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        venv = make_venv(Path(tmp))
        print(f"  note installed {ROOT.name}/lib/spatialdds into a temporary "
              f"environment; `python3` below is {venv}/bin/python3\n")
        env = dict(os.environ,
                   PATH=os.pathsep.join([str(venv / "bin"),
                                         os.environ.get("PATH", "")]),
                   VIRTUAL_ENV=str(venv))
        env.pop("PYTHONHOME", None)
        for doc in DOCS:
            rc = walk(doc, checks, env, args.refill)
            if rc is not None:
                return rc
    return report("quickstart", checks,
                  "every page can be pasted, not merely interpreted")


def walk(doc: Path, checks: list, env: dict,
         refill: bool = False) -> int | None:
    name = doc.relative_to(ROOT).as_posix()
    blocks = fences(doc.read_text())
    plain = [b for b in blocks if b[1] == "sh" and not b[3]]
    assert plain, f"{doc} has no unmarked sh fence to take a serve line from"
    # There is deliberately no export fence to find any more. A document that
    # kept one invited every block after it to depend on it, which is the
    # defect this gate exists to catch.
    # The page may stand the endpoint up more than once -- the publish section
    # restarts it with the write surface on -- so the walk is a sequence of
    # phases in document order: an unmarked fence holding a serve line opens a
    # phase, and the marked fences after it belong to that phase. A reader
    # stops one gateway before starting the next, and so does this.
    phases: list[tuple[str, list]] = []
    for n, b in enumerate(blocks):
        if b[1] == "sh" and not b[3] and any(f in l for l in b[2]
                                             for f in SERVE_FORMS):
            i = next(k for k, l in enumerate(b[2])
                     if any(f in l for f in SERVE_FORMS))
            phases.append((logical(b[2], i), []))
            # Each phase's own recording and port. A document may serve more
            # than one recording -- examples/curl.md serves the fountain and
            # then the retail sample -- and taking either from the first serve
            # line checks the wrong thing for every phase after it.
        elif b[3] and b[1] == "sh" and phases:
            nxt = blocks[n + 1] if n + 1 < len(blocks) else None
            want = nxt[2] if nxt and nxt[1] == "console" else None
            # The console fence's own bounds, so --refill can replace its body
            # without reparsing: `fences` reports the line of the opening
            # ``` , and the body is what follows it.
            span = ((nxt[0], nxt[0] + len(nxt[2]))
                    if nxt and nxt[1] == "console" else None)
            phases[-1][1].append((b[0], b[1], b[2], b[3], want, span))

    # Every pip fence on the page, in document order. A fence naming a
    # remote package must name the from-source line beside it, and the
    # from-source line is the one this gate runs (in `make_venv`, once).
    pip_fences = [b for b in blocks if b[1] == "sh"
                  and any(l.strip().startswith("pip install") for l in b[2])]
    bad_pairs, named = [], []
    for b in pip_fences:
        local, remote = local_installs(b[2])
        named += [l.split()[2] for l in local + remote]
        if remote and not local:
            bad_pairs.append(f"{name}:{b[0]} installs {remote[0].split()[2]} "
                             f"with no from-source line beside it")
    if pip_fences:
        checks.append((f"{name}: every pip line naming a release has the "
                       f"from-source line beside it "
                       f"({len(pip_fences)} install fence(s))",
                       not bad_pairs, "; ".join(bad_pairs)))
    # And the third-party prerequisites the page names are importable.
    plain = [l.split()[2:] for b in pip_fences for l in b[2]
             if l.strip().startswith("pip install")
             and not any(a.startswith(("\"", ".", "/")) for a in l.split()[2:])
             and "spatialdds" not in l]
    missing = [q for q in (plain[0] if plain else [])
               if __import__("importlib.util", fromlist=["util"])
               .find_spec(q) is None]
    if plain:
        checks.append((f"{name}: the prerequisites it names are importable "
                       f"({', '.join(plain[0])})",
                       not missing, f"not installed: {missing}"))

    def served_of(line: str):
        """What a serve line serves, and on which port.

        A recording is identified by its bytes rather than its path: `--demo`
        serves the copy packaged inside the distribution, which is the same
        recording as `samples/fountain-anchors.mcap` under a different name,
        and comparing paths would call that a mismatch. A line naming no
        recording serves an empty registry, which 0.2.0 made the default and
        is a legitimate thing for a page to show.
        """
        mm = re.search(r"--mcap\s+(\S+)", line)
        pm = re.search(r"--port\s+(\d+)", line)
        port = int(pm.group(1)) if pm else 8787
        if mm:
            return digest_of((ROOT / mm.group(1)).resolve()), port
        if "--demo" in line:
            return digest_of(DEMO_MCAP), port
        return "empty", port

    # Every marker on the page must introduce a runnable command fence. A
    # marker sitting on a `console` fence means the page still carries the
    # prompt-and-output shape this gate exists to reject, and finding zero
    # tiers to run must fail rather than pass on nothing -- which is exactly
    # what this gate did on its first run.
    # Each mode has one fence type that can hold a command alone: a shell tier
    # is ```sh, a console tier is ```js. A marker on a ```console fence means
    # the page still carries the prompt-and-output shape, where the copy button
    # yields a prompt and expected output along with the command.
    WANT = {"sh": "sh", "sh-ok": "sh", "py": "sh", "py-fails": "sh",
            "js": "js", "js-ok": "js"}
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
    wrong = []
    # (kept: each phase's own recording and port)
    for sl, blks in phases:
        want, _p = served_of(sl)
        for b in blks:
            mcap = ex.RECORDINGS[b[3].split()[0]][0]
            got = "empty" if mcap is None else digest_of(mcap.resolve())
            if got != want:
                wrong.append(f"{name}:{b[0]} names {b[3].split()[0]!r} "
                             f"({got[:8]}) under a serve line serving "
                             f"{want[:8]}")
    served_names = sorted({served_of(sl)[0][:8] for sl, _ in phases})
    checks.append((f"{name}: every tier names the recording its own phase's "
                   f"serve line serves ({', '.join(served_names)}; "
                   f"{len(runnable)} tier(s))", not wrong, "; ".join(wrong)))

    checks.append((f"{name}: stands the endpoint up {len(phases)} time(s), "
                   f"and every marked shell tier belongs to one "
                   f"({sum(len(c) for _s, c in phases)} of {len(runnable)})",
                   bool(phases)
                   and sum(len(c) for _s, c in phases) == len(runnable),
                   f"{[(s_.split()[-1], len(c)) for s_, c in phases]}"))

    # A journal left by an earlier run is exactly the stale-state hazard this
    # repository keeps naming: the page's first phase would replay records
    # nobody published in this walk, and every count after it would be wrong.
    # The page carries `rm -f` for a reader; the gate does the same, and says
    # so rather than doing it quietly.
    # Every internal link on the page resolves. Cheap, and the failure it
    # catches is the one a reader meets first: the front page lost four
    # sections to docs/ on 2026-10-16, and a link that moved with them would
    # have been a 404 on the repository's front door.
    links = re.findall(r"\[[^\]]+\]\(([^)#]+)\)", doc.read_text())
    internal = [l for l in links if not l.startswith(("http://", "https://",
                                                      "mailto:"))]
    broken = [l for l in internal
              if not (doc.parent / l.rstrip("/")).exists()]
    checks.append((f"{name}: all {len(internal)} internal link(s) resolve",
                   not broken, f"{broken}"))

    journals = [Path(jm.group(1))
                for sl, _c in phases
                for jm in [re.search(r"--journal\s+(\S+)", sl)] if jm]
    # A serve line with --publish and no --journal writes one into the
    # current directory, which for this gate is the checkout. Both are
    # removed before the walk and the implicit one after it: a journal left
    # behind is state the next run would replay, and a checkout is not a
    # place to leave files.
    if any("--publish" in sl for sl, _c in phases):
        journals.append(ROOT / "spatialdds-registry.mcap")
    for jp in journals:
        if jp.is_file():
            jp.unlink()
            print(f"  note removed {jp}, a journal from an earlier run")

    busy = [(p_, listening(p_)) for _sl, _c in phases
            for p_ in [served_of(_sl)[1]] if listening(p_)]
    if busy:
        checks.append((f"{name}: every port its serve lines name is free",
                       False,
                       f"port {busy[0][0]} held by: {busy[0][1][:90]}"))
        return report("quickstart", checks,
                      f"cannot observe {name} on its own ports")

    # **Nothing is prepended.** The previous version pasted the document's
    # export fence in front of every command block, which is precisely why it
    # never saw what a person sees: a reader opens a second terminal, scrolls
    # to the command they want, and pastes that block alone. Every one of the
    # ten command blocks in this repository failed that way while the gate was
    # green, because the gate's shell never forgot a variable. A block that
    # needs an export now has to carry it.
    writes: list[tuple[tuple[int, int], list[str]]] = []
    for serve_line, cmd_blocks in phases:
        rc = await_phase(name, serve_line, cmd_blocks,
                         served_of(serve_line)[1], checks, env, writes)
        if rc is not None:
            return rc
    for jp in journals:
        if jp.is_file() and jp.parent == ROOT:
            jp.unlink()
            print(f"  note removed {jp.name}, which the walk's own serve line "
                  f"wrote into the checkout")
    if refill and writes:
        lines = doc.read_text().splitlines()
        for (a, b), got in sorted(writes, key=lambda x: -x[0][0]):
            lines[a:b] = got
        doc.write_text("\n".join(lines) + "\n")
        print(f"  refilled {len(writes)} output fence(s) in {name}")
    return None


def await_phase(name: str, serve: str, cmd_blocks: list,
                port: int, checks: list, env: dict,
                writes: list) -> int | None:
    # The serve line is run as written, through the environment the install
    # produced: `python3 tools/run_gateway.py ...` and
    # `python -m spatialdds.serve ...` are both just the first word plus its
    # arguments, and the first word is whichever python the documented
    # install put on PATH.
    argv = serve.split()
    exe = env["PATH"].split(os.pathsep)[0] + "/" + argv[0]
    child = subprocess.Popen([exe if Path(exe).exists() else argv[0]]
                             + argv[1:], cwd=ROOT, env=env,
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

        for line, _tag, body, marker, want, span in cmd_blocks:
            # A fence marked `sh-ok` documents no output: `gates/examples.py`
            # has the same mode, for the same reason -- the front page's
            # closing `curl` returns the whole search response, and a
            # storefront does not paste that. Here the claim is the same one a
            # reader makes when they paste it: it runs, and it prints
            # something.
            unpinned = marker.split()[1:2] == ["sh-ok"]
            if want is None and not unpinned:
                checks.append((f"{name}:{line}: its output fence follows it",
                               False, "no ```console fence after the command"))
                continue
            script = "\n".join(body)
            r = subprocess.run([SHELL, "-c", script], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=180)
            have = [l.rstrip() for l in r.stdout.splitlines()]
            if unpinned:
                err = (r.stderr.strip().splitlines() or [""])[-1][:110]
                checks.append((f"{name}:{line} pasted verbatim, output "
                               f"deliberately not pinned",
                               r.returncode == 0
                               and any(l.strip() for l in have),
                               f"exit {r.returncode}, "
                               f"{len(have)} line(s); {err}"))
                continue
            exp = [l.rstrip() for l in want]
            ok = have == exp and any(l.strip() for l in have)
            if have == exp and not any(l.strip() for l in have):
                # An empty fence matching an empty run is the one way this
                # gate could report success for a block that did nothing --
                # and an empty fence is exactly what the last bad refill left
                # behind. A documented command prints something or it is not
                # documented.
                detail_empty = (r.stderr.strip().splitlines() or
                                ["printed nothing and documents nothing"])[-1]
                checks.append((f"{name}:{line} pasted verbatim", False,
                               f"both the command and its fence printed "
                               f"nothing; stderr ends: "
                               f"{detail_empty.strip()[:120]}"))
                continue
            detail = ""
            if not ok:
                bad = next((f"line {i + 1}: want {w!r}, got {g!r}"
                            for i, (w, g) in enumerate(zip(exp, have))
                            if w != g), f"{len(exp)} want, {len(have)} got")
                err = (r.stderr.strip().splitlines() or [""])[0][:100]
                detail = bad + (f"; stderr: {err}" if err else "")
            checks.append((f"{name}:{line} pasted verbatim", ok, detail))
            # Only a fence that would actually change: reporting "refilled"
            # for a fence whose content is already right is a count nobody
            # can act on.
            if span is not None and any(l.strip() for l in have) \
                    and have != exp:
                writes.append((span, have))
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
    return None


if __name__ == "__main__":
    sys.exit(main())
