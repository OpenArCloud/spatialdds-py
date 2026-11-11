#!/usr/bin/env python3
"""The MCP server guide, executed: every tool result it shows is one that ran.

docs/mcp-server.md is the public page for `spatialdds-mcp`. This gate runs
it the way a reader's MCP client would, in document order, through the
official MCP SDK's client over stdio (and, for its HTTP section, over
streamable HTTP), and fails on drift either way: an example that no longer
runs, or a result that no longer matches the one the page prints.

The page carries markers, HTML comments that render as nothing, each on a
line of its own and bound to the fence that follows it:

    <!-- mcp: install -->              the pip line; checked, not run
    <!-- mcp: config NAME -->          a client configuration; its command
                                       is launched, as that client would
    <!-- mcp: call sandbox -->         a ```json fence naming a tool and its
    <!-- mcp: call venue -->           arguments, called on the server the
                                       `claude-desktop` (sandbox) or `venue`
                                       configuration starts; the next fence
                                       is the result's first text block
    <!-- mcp: call venue json PATH --> the same, and the next ```json fence
                                       is the structured result at PATH
    <!-- mcp: venue ACTION ... -->     what the venue's bus does next:
                                       `writer-lost TYPE ASSERTER`,
                                       `withdraw TYPE KEY REASON...`,
                                       `write TYPE` (the next ```json fence)
    <!-- mcp: http start -->           the ```sh fence that starts --http
    <!-- mcp: http python -->          a ```python fence run against it; the
                                       next ```console fence is its stdout

What differs from a reader's run, and only that:

* **`spatialdds-mcp` is this checkout's**, `python -m spatialdds.mcp_server`
  with the package on PYTHONPATH, so the gate tests the code beside the page.
* **The venue is a copy.** `samples/mcp/venue.json`, served by `_mcp.Venue`
  as the demo's bridge serves the live one; the venue configuration's
  `http://localhost:8089` is that copy's loopback address, and its
  `127.0.0.1:<port>` is printed back as `localhost:8089`.
* **The HTTP section's port** is a free one in place of 8790.

**Two values are checked, not compared.** An age is now minus a stamp, so it
differs on every run: each `(… ago)` beside a stamp, and each `age_s`, must
be what the server's own definition gives for the stamp printed with it at
the time of the call, and is then compared as the page prints it. A time
the run itself produced (when a watch began, when liveliness was lost) is
accepted where the page prints a time, and no other time is: a record's
stamp must match exactly.

The sandbox's examples need the sandbox: with it unreachable they are
skipped, naming it, and the venue's still run.

    gates/mcp_guide.py            run the page and compare
    gates/mcp_guide.py --write    run it and write the results into the page
                                  (for review, then commit)

Needs the [mcp] extra, and cyclonedds for the venue's relationship type.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import _mcp                                                   # noqa: E402
from harness import SKIP, report                              # noqa: E402
from spatialdds import mcp_server                             # noqa: E402

GUIDE = ROOT / "docs" / "mcp-server.md"
SANDBOX = "https://demo.spatialdds.org"
VENUE_AS_PRINTED = "localhost:8089"
HTTP_PORT_AS_PRINTED = "8790"
MARKER = re.compile(r"^<!--\s*mcp:\s*(.+?)\s*-->$")
FENCE = re.compile(r"^```(\w*)\s*$")
ISO = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
STAMPED = re.compile(rf"stamped ({ISO}) \(([^)]*)\)")


def version() -> str:
    text = (ROOT / "lib" / "spatialdds" / "spatialdds" / "__init__.py") \
        .read_text()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


# ---- the document ----------------------------------------------------------

class Block:
    def __init__(self, kind: str, args: list[str], line: int) -> None:
        self.kind, self.args, self.line = kind, args, line
        self.body = ""
        self.lang = ""
        self.expected: str | None = None
        self.out_span: tuple[int, int] | None = None


def fences(lines: list[str]) -> list[tuple[int, int, str, str]]:
    """(start, end, lang, body) for every fence: start and end are the
    indices of the opening and closing lines."""
    out, i = [], 0
    while i < len(lines):
        f = FENCE.match(lines[i])
        if f:
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            out.append((i, j, f.group(1), "\n".join(lines[i + 1:j])))
            i = j + 1
            continue
        i += 1
    return out


def parse(lines: list[str]) -> tuple[list[Block], list[int]]:
    """Blocks in document order, and the lines of unmarked runnable fences."""
    fs = fences(lines)
    by_start = {f[0]: f for f in fs}
    starts = [f[0] for f in fs]
    used: set[int] = set()
    blocks: list[Block] = []

    def next_fence(after: int):
        for s in starts:
            if s > after and s not in used:
                used.add(s)
                return by_start[s]
        raise ValueError(f"no fence after line {after + 1}")

    for i, line in enumerate(lines):
        m = MARKER.match(line)
        if not m:
            continue
        words = m.group(1).split()
        b = Block(words[0], words[1:], i + 1)
        if b.kind == "venue" and b.args[0] != "write":
            blocks.append(b)
            continue
        s, e, lang, body = next_fence(i)
        b.lang, b.body = lang, body
        if b.kind == "call" or (b.kind == "http" and b.args[0] == "python"):
            s2, e2, _l, out = next_fence(e)
            b.expected, b.out_span = out, (s2 + 1, e2)
        blocks.append(b)
    unmarked = [f[0] + 1 for f in fs if f[0] not in used
                and f[2] in ("sh", "python", "bash", "json")]
    return blocks, unmarked


def tool_table(lines: list[str]) -> list[str]:
    """The tool names in the table under "## The tools"."""
    names, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == "## The tools"
        elif inside and line.startswith("| `"):
            names.append(line.split("|")[1].strip().strip("`"))
    return names


# ---- comparison -------------------------------------------------------------

def spoken(age: float) -> str:
    """The age phrase, as the page states the rule, written again here.

    Not the module's `age_phrase`: a check that asked the code under test
    what the right phrase is would agree with any phrase it printed. Under
    90 s in seconds, under 90 min in minutes, under 36 h in hours to a
    tenth, else days to a tenth; a negative age is "stamped … in the
    future".
    """
    if age < 0:
        return f"stamped {spoken(-age).removesuffix(' ago')} in the future"
    for limit, unit, div, fmt in ((90, "s", 1, "{:.0f}"),
                                  (90 * 60, "min", 60, "{:.0f}"),
                                  (36 * 3600, "h", 3600, "{:.1f}"),
                                  (float("inf"), "d", 86400, "{:.1f}")):
        if age < limit:
            return fmt.format(age / div) + f" {unit} ago"
    raise AssertionError(age)


def stamp_of(stamp: dict) -> float:
    """`{"sec": "…", "nanosec": n}`, read here rather than by the module."""
    return int(stamp["sec"]) + int(stamp["nanosec"]) / 1e9


def epoch(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


class Clock:
    """The run's start, and each call's window, for the two checked values."""

    def __init__(self) -> None:
        self.start = time.time()


def compare_text(expected: str, got: str, t0: float, t1: float,
                 started: float) -> tuple[bool, str]:
    """Exact, except a run-time time where the page prints a time, and an
    age phrase that is right for its stamp (then taken as printed)."""
    e_parts = re.split(f"({ISO})", expected.strip("\n"))
    g_parts = re.split(f"({ISO})", got.strip("\n"))
    if len(e_parts) != len(g_parts):
        return False, (f"the page prints {len(e_parts) // 2} time(s), the "
                       f"run {len(g_parts) // 2}; {first_diff(expected, got)}")
    for i in range(1, len(g_parts), 2):
        if g_parts[i] == e_parts[i]:
            continue
        if epoch(g_parts[i]) >= started - 2:
            g_parts[i] = e_parts[i]
        else:
            return False, (f"a record's time differs: page {e_parts[i]}, "
                           f"ran {g_parts[i]}")
    got = "".join(g_parts)
    e_ages, g_ages = STAMPED.findall(expected), STAMPED.findall(got)
    if len(e_ages) != len(g_ages):
        return False, first_diff(expected, got)
    pieces = STAMPED.split(got)
    for n, ((stamp, phrase), (_es, ephrase)) in enumerate(zip(g_ages,
                                                              e_ages)):
        ts = epoch(stamp)
        allowed = {spoken(round(t - ts, 1))
                   for t in (t0 - 1, t0, t1, t1 + 1)}
        if phrase not in allowed:
            return False, (f"the age beside {stamp} is {phrase!r}; for a "
                           f"call at {t0:.0f}..{t1:.0f} it is one of "
                           f"{sorted(allowed)}")
        pieces[3 * n + 2] = ephrase
    rebuilt = "".join(p if k % 3 == 0 else
                      (f"stamped {p} (" if k % 3 == 1 else p + ")")
                      for k, p in enumerate(pieces))
    ok = norm(rebuilt) == norm(expected)
    return ok, "identical" if ok else first_diff(expected, rebuilt)


def compare_json(expected: str, got: dict, t0: float, t1: float,
                 started: float) -> tuple[bool, str]:
    try:
        want = json.loads(expected)
    except ValueError as e:
        return False, f"the page's JSON does not parse: {e}"
    problems: list[str] = []

    def walk(g, w, where):
        if isinstance(g, dict) and isinstance(w, dict):
            out = {}
            for k, v in g.items():
                # An entity row carries its stamp beside its age; a claim
                # row carries it in the verbatim record, under `claim`.
                stamp = g.get("stamp") or (g.get("claim") or {}).get("stamp")
                if k == "age_s" and isinstance(v, (int, float)) and stamp:
                    ts = stamp_of(stamp)
                    if not (t0 - ts - 1.5 <= v <= t1 - ts + 1.5):
                        problems.append(f"{where}.age_s is {v}, not the "
                                        f"call's age of its stamp")
                    out[k] = w.get(k, v)
                else:
                    out[k] = walk(v, w.get(k), f"{where}.{k}")
            return out
        if isinstance(g, list) and isinstance(w, list) and len(g) == len(w):
            return [walk(a, b, f"{where}[{i}]")
                    for i, (a, b) in enumerate(zip(g, w))]
        if isinstance(g, str) and isinstance(w, str) and g != w \
                and re.fullmatch(ISO, g) and re.fullmatch(ISO, w) \
                and epoch(g) >= started - 2:
            return w
        if isinstance(g, str) and isinstance(w, str) and g != w \
                and STAMPED.search(g):
            # A rendering inside the JSON: its age is checked as the text
            # block's are.
            ok, why = compare_text(w, g, t0, t1, started)
            if ok:
                return w
            problems.append(f"{where}: {why}")
        return g
    rebuilt = walk(got, want, "result")
    if problems:
        return False, "; ".join(problems)
    a = json.dumps(rebuilt, indent=1, sort_keys=True)
    b = json.dumps(want, indent=1, sort_keys=True)
    return a == b, "identical" if a == b else first_diff(b, a)


def norm(text: str) -> str:
    return "\n".join(l.rstrip() for l in text.strip("\n").splitlines())


def first_diff(expected: str, got: str) -> str:
    e, g = norm(expected).splitlines(), norm(got).splitlines()
    for i in range(max(len(e), len(g))):
        x = e[i] if i < len(e) else "<nothing>"
        y = g[i] if i < len(g) else "<nothing>"
        if x != y:
            return f"first difference at line {i + 1}: page {x!r}, ran {y!r}"
    return "identical"


def at(doc: dict, path: str):
    for part in path.split("."):
        doc = doc[int(part)] if part.isdigit() else doc[part]
    return doc


# ---- the run ---------------------------------------------------------------

def reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(urllib.request.Request(
            url + "/.well-known/spatialdds/search", method="GET"), timeout=10)
        return True
    except OSError:
        return False


def config_args(b: Block) -> tuple[str, list[str]]:
    doc = json.loads(b.body)
    (name, entry), = doc["mcpServers"].items()
    return entry["command"], list(entry.get("args", []))


async def drained(venue: _mcp.Venue, type_seg: str) -> None:
    """Every subscriber's queue handed to its socket, then a moment for the
    server's watch to take it off the wire."""
    res = venue.res[type_seg]
    for _ in range(100):
        qs = venue._in_loop(lambda: list(venue.app["subs"].get(res.topic,
                                                               ())))
        if all(q.empty() for q in qs):
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)


async def run_guide(blocks: list[Block], checks: list, write: bool,
                    online: bool) -> list[tuple[Block, str]]:
    started = time.time()
    outputs: list[tuple[Block, str]] = []
    configs = {b.args[0]: b for b in blocks if b.kind == "config"}
    venue = _mcp.Venue()
    http = None
    try:
        async with AsyncExitStack() as stack:
            clients: dict[str, object] = {}
            for name in ("claude-desktop", "claude-code"):
                command, args = config_args(configs[name])
                checks.append((f"the {name} configuration runs "
                               f"`spatialdds-mcp`",
                               command == "spatialdds-mcp", command))
                c = await stack.enter_async_context(_mcp.stdio(args))
                tools = sorted(t.name for t in (await c.list_tools()).tools)
                checks.append((f"the {name} configuration starts a server "
                               f"with the five tools",
                               tools == sorted(mcp_server.TOOLS), str(tools)))
                clients.setdefault("sandbox", c)
            _cmd, vargs = config_args(configs["venue"])
            vargs = [a.replace(f"http://{VENUE_AS_PRINTED}", venue.base)
                     for a in vargs]
            clients["venue"] = await stack.enter_async_context(
                _mcp.stdio(vargs))
            # The watch must have its replay before the bus moves: what a
            # reader whose server had been running for a while would have.
            ok = _mcp.wait_for(lambda: venue.subscribers("entity") >= 1
                               and venue.subscribers("oarc.relationship")
                               >= 1)
            await asyncio.sleep(0.5)
            checks.append(("the venue's server watches both world lanes "
                           "before the first example", ok,
                           "no subscription arrived"))

            for b in blocks:
                where = f"line {b.line}"
                if b.kind == "venue":
                    act, *rest = b.args
                    seg = rest[0]
                    if act == "writer-lost":
                        who = rest[1]
                        keys = [k for k, r in venue.records(seg).items()
                                if who in (r.get("asserter"),
                                           r.get("source_id"))]
                        for k in keys:
                            venue.liveliness(seg, k, False)
                        checks.append((f"{where}: the venue's writer "
                                       f"{who} is lost for each record it "
                                       f"wrote", bool(keys), "none found"))
                    elif act == "withdraw":
                        venue.withdraw(seg, rest[1], " ".join(rest[2:]))
                    elif act == "write":
                        venue.write(seg, json.loads(b.body))
                    await drained(venue, seg)
                    continue
                if b.kind == "call":
                    backend = b.args[0]
                    req = json.loads(b.body)
                    if backend == "sandbox" and not online:
                        checks.append((f"{where}: `{req['tool']}` against "
                                       f"the sandbox", SKIP,
                                       f"skipped, not failed: {SANDBOX} is "
                                       f"unreachable from here"))
                        continue
                    t0 = time.time()
                    r = await clients[backend].call_tool(
                        req["tool"], req.get("arguments", {}))
                    t1 = time.time()
                    if len(b.args) > 2 and b.args[1] == "json":
                        got_doc = _mcp.doc_of(r)
                        got_doc = json.loads(json.dumps(got_doc).replace(
                            f"127.0.0.1:{venue.port}", VENUE_AS_PRINTED))
                        part = at(got_doc, b.args[2])
                        outputs.append((b, json.dumps(part, indent=1,
                                                      sort_keys=True)))
                        ok, why = compare_json(b.expected, part, t0, t1,
                                               started)
                    else:
                        got = _mcp.text_of(r).replace(
                            f"127.0.0.1:{venue.port}", VENUE_AS_PRINTED)
                        outputs.append((b, got))
                        ok, why = compare_text(b.expected, got, t0, t1,
                                               started)
                    if r.is_error:
                        ok, why = False, f"the tool failed: {_mcp.text_of(r)}"
                    checks.append((f"{where}: `{req['tool']}` "
                                   f"{json.dumps(req.get('arguments', {}))}"
                                   f" prints what the page shows", ok, why))
                    continue
                if b.kind == "http" and b.args[0] == "start":
                    argv = shlex.split(b.body.strip())
                    if not online:
                        checks.append((f"{where}: --http against the "
                                       f"sandbox", SKIP,
                                       f"skipped, not failed: {SANDBOX} is "
                                       f"unreachable from here"))
                        continue
                    i = argv.index("--http")
                    host = argv[i + 1].rsplit(":", 1)[0]
                    checks.append((f"{where}: the page starts "
                                   f"`spatialdds-mcp --http` on "
                                   f"{host}:{HTTP_PORT_AS_PRINTED}",
                                   argv[0] == "spatialdds-mcp"
                                   and argv[i + 1].endswith(
                                       ":" + HTTP_PORT_AS_PRINTED),
                                   " ".join(argv)))
                    rest = argv[1:i] + argv[i + 2:]
                    http = _mcp.HttpServer(rest)
                    continue
                if b.kind == "http" and b.args[0] == "python":
                    if http is None:
                        checks.append((f"{where}: the HTTP client example",
                                       SKIP, "skipped, not failed: no "
                                             "server (the sandbox is "
                                             "unreachable)"))
                        continue
                    code = b.body.replace(f"127.0.0.1:{HTTP_PORT_AS_PRINTED}",
                                          f"127.0.0.1:{http.port}")
                    r = subprocess.run([sys.executable, "-"], input=code,
                                       capture_output=True, text=True,
                                       env=_mcp.server_env(), timeout=120)
                    outputs.append((b, r.stdout))
                    ok = r.returncode == 0 and norm(r.stdout) == norm(
                        b.expected)
                    checks.append((f"{where}: the HTTP client example runs "
                                   f"and prints what the page shows", ok,
                                   f"exit {r.returncode}; "
                                   f"{first_diff(b.expected, r.stdout)}"
                                   + (f"; stderr: {r.stderr.strip()[-300:]}"
                                      if r.returncode else "")))
    finally:
        if http is not None:
            http.stop()
        venue.stop()
    creds = [r for r in venue.requests if r[2]]
    checks.append(("the venue's server read as a stranger: no request it "
                   "made carried a credential",
                   not creds and bool(venue.requests),
                   f"{len(creds)} of {len(venue.requests)} carried one: "
                   f"{creds[:3]}"))
    return outputs


def write_outputs(lines: list[str], outputs: list[tuple[Block, str]]) -> None:
    for b, out in sorted(outputs, key=lambda x: -(x[0].out_span or (0, 0))[0]):
        if b.out_span is None:
            continue
        s, e = b.out_span
        lines[s:e] = norm(out).splitlines()
    GUIDE.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    lines = GUIDE.read_text().split("\n")
    blocks, unmarked = parse(lines)
    checks: list = [("every runnable fence carries a marker, so none goes "
                     "untested", not unmarked,
                     f"unmarked fences at lines {unmarked}")]
    v = version()
    install = [b for b in blocks if b.kind == "install"]
    checks.append((f"the install line names the extra and pins this "
                   f"checkout's version ({v})",
                   len(install) == 1 and install[0].body.strip()
                   == f'pip install "spatialdds[mcp]=={v}"',
                   install[0].body if install else "no install block"))
    # The whole PEP 440 version: three numbers alone read 0.8.3.post1
    # as 0.8.3, which failed a page that named its version exactly.
    named = set(re.findall(r"spatialdds(?:\[mcp\])?(?:`|==)\s?"
                           r"(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?)",
                           GUIDE.read_text()))
    checks.append((f"every spatialdds version the page names is {v}",
                   named == {v}, f"named: {sorted(named)}"))
    table = tool_table(lines)
    checks.append(("the page's tool table is the server's five tools, in "
                   "order", tuple(table) == mcp_server.TOOLS, str(table)))
    called = {json.loads(b.body)["tool"] for b in blocks if b.kind == "call"
              and b.args[0] == "sandbox"}
    checks.append(("every tool has a worked example against the sandbox",
                   called == set(mcp_server.TOOLS),
                   f"missing: {sorted(set(mcp_server.TOOLS) - called)}"))
    online = reachable(SANDBOX)
    outputs = asyncio.run(run_guide(blocks, checks, a.write, online))
    if a.write:
        write_outputs(lines, outputs)
        print(f"wrote {len(outputs)} result(s) into {GUIDE.name}; review the "
              f"diff, then run without --write")
        return 0
    return report("the MCP server guide, executed", checks,
                  "every example ran as printed, through the MCP SDK's own "
                  "client")


if __name__ == "__main__":
    sys.exit(main())
