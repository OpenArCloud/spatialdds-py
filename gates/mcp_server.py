#!/usr/bin/env python3
"""`spatialdds-mcp`: an extra that changes nothing else, and a stranger's client.

Five groups of checks, each a claim docs/mcp-server.md or the module's
docstring makes:

A. **Packaging.** `[mcp]` brings the MCP SDK and nothing else; the base
   install's dependencies are the 0.8.2 tag's, exactly; `spatialdds-mcp` is
   installed. In a fresh virtual environment holding the built wheel and not
   `mcp`, the package imports, `spatialdds-mcp` refuses saying which line to
   run, and the client package's own gate (`gates/library.py`) passes. That
   part needs a package index and is skipped, naming it, without one.
B. **The rule.** The server imports no DDS, no gateway and no server
   dependency, by its import statements and by `sys.modules` after a tool
   call; and every request it sends carries no credential.
C. **What it says, against a venue whose bus moves** (`_mcp.Venue`): a lost
   writer is reported and the record kept; a replayed record is not evidence
   of a live writer; a withdrawal is kept and marked; two asserters stay two
   claims, side by side; every claim is the binding's record, verbatim; ages
   are now minus the stamp; `near` measures to the box; an unreadable lane or
   endpoint is named, never left out. Over stdio, with both handshakes.
D. **HTTP.** The five tools over streamable HTTP; the per-client throttle,
   behind a front as well as direct; and a host name the server was not told
   to answer to is refused.
E. **The transcripts** in samples/mcp: their checksums, that each calls all
   five tools over the transport it names, and that the live venue's claims
   came through verbatim, basis and asserter included, against the binding's
   own answer recorded beside them.

    gates/mcp_server.py [--only ABCDE]

Needs the [mcp] extra, and cyclonedds for the venue's relationship type.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import _mcp                                                   # noqa: E402
from harness import SKIP, report                              # noqa: E402

LIB = ROOT / "lib" / "spatialdds"
MODULE = LIB / "spatialdds" / "mcp_server.py"
GUIDE = ROOT / "docs" / "mcp-server.md"
#: The release the base install is held to: the last one without the extra.
TAG = "spatialdds-v0.8.2"
#: Where the tag is read from. The plants run this gate in a copy of the
#: tree, which has no history of its own.
GIT = Path(os.environ.get("SPATIALDDS_GATE_GIT", str(ROOT)))
#: What the server may import from its own package: the client half.
OWN_ALLOWED = {".", ".client", ".errors", ".events"}
FORBIDDEN = ("spatialdds.server", "spatialdds.dds", "cyclonedds", "aiohttp",
             "mcap", "jsonschema")
SCENE = "ut-austin-littlefield"
CLAIMS = "oarc.relationship"


# ---- A. packaging ----------------------------------------------------------

def packaging(checks: list) -> None:
    proj = tomllib.loads((LIB / "pyproject.toml").read_text())["project"]
    tagged = tomllib.loads(subprocess.run(
        ["git", "-C", str(GIT), "show", f"{TAG}:lib/spatialdds/pyproject.toml"],
        capture_output=True, text=True, check=True).stdout)["project"]
    checks.append(("the [mcp] extra is the MCP SDK and nothing else",
                   proj["optional-dependencies"].get("mcp") == ["mcp>=2.3,<3"],
                   str(proj["optional-dependencies"].get("mcp"))))
    checks.append((f"the base install's dependencies are {TAG}'s, exactly",
                   proj["dependencies"] == tagged["dependencies"],
                   f"now {proj['dependencies']}, {TAG} "
                   f"{tagged['dependencies']}"))
    others = {k: v for k, v in proj["optional-dependencies"].items()
              if k != "mcp"}
    checks.append((f"every other extra is {TAG}'s, exactly",
                   others == tagged["optional-dependencies"],
                   f"now {sorted(others)}"))
    checks.append(("`spatialdds-mcp` is installed, as "
                   "spatialdds.mcp_server:main",
                   proj["scripts"].get("spatialdds-mcp")
                   == "spatialdds.mcp_server:main", str(proj["scripts"])))
    clean_install(checks)


def index_reachable() -> bool:
    try:
        urllib.request.urlopen("https://pypi.org/simple/requests/",
                               timeout=10)
        return True
    except OSError:
        return False


def clean_install(checks: list) -> None:
    """A fresh venv: the built wheel, every extra but [mcp], and no `mcp`."""
    if not index_reachable():
        checks.append(("a fresh environment without mcp: imports, refuses, "
                       "and passes the library gate", SKIP,
                       "skipped, not failed: no package index reachable "
                       "for the base dependencies"))
        return
    # Clean means clean: run_all puts the checkout on PYTHONPATH, and with
    # it there pip sees spatialdds as installed and skips the wheel.
    clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="mcp-base-") as tmp:
        tmp = Path(tmp)
        r = subprocess.run([sys.executable, "-m", "pip", "wheel", "-q",
                            "--no-deps", "-w", str(tmp / "dist"), str(LIB)],
                           capture_output=True, text=True, env=clean)
        wheels = list((tmp / "dist").glob("spatialdds-*.whl"))
        if r.returncode or len(wheels) != 1:
            checks.append(("the wheel builds", False, r.stderr[-400:]))
            return
        wheel = wheels[0]
        import zipfile
        with zipfile.ZipFile(wheel) as z:
            meta = next(z.read(n).decode() for n in z.namelist()
                        if n.endswith(".dist-info/METADATA"))
            ep = next(z.read(n).decode() for n in z.namelist()
                      if n.endswith(".dist-info/entry_points.txt"))
        reqs = [l.split(":", 1)[1].strip() for l in meta.splitlines()
                if l.startswith("Requires-Dist:")]
        base = sorted(q for q in reqs if "extra ==" not in q)
        mcp_reqs = sorted(q.split(";")[0].strip() for q in reqs
                          if 'extra == "mcp"' in q)
        checks.append(("the wheel's base Requires-Dist is two lines, the "
                       "tag's, and the extra's is the SDK alone",
                       base == ["requests>=2.25", "websockets>=12"]
                       and mcp_reqs == ["mcp<3,>=2.3"],
                       f"base {base}; mcp {mcp_reqs}"))
        checks.append(("the wheel installs `spatialdds-mcp`",
                       "spatialdds-mcp = spatialdds.mcp_server:main" in ep,
                       ep.strip()))
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                       env=clean)
        py = venv / "bin" / "python"
        # Every other extra, and not this one: `gates/library.py` resolves
        # every public name, `sd.dds` included, so it needs [dds] as it
        # always has. What is absent is `mcp`, and only `mcp`.
        r = subprocess.run([str(py), "-m", "pip", "install", "-q",
                            f"{wheel}[server,dds]"], capture_output=True,
                           text=True, env=clean)
        if r.returncode:
            checks.append(("the wheel installs in a fresh environment",
                           False, r.stderr[-400:]))
            return
        has_mcp = subprocess.run([str(py), "-c", "import mcp"],
                                 capture_output=True, env=clean,
                                 cwd=tmp).returncode == 0
        imp = subprocess.run(
            [str(py), "-c",
             "import spatialdds as sd, spatialdds.mcp_server as m, sys\n"
             "sd.connect('https://example.org')\n"
             "print(sorted(m.TOOLS), 'mcp' in sys.modules)"],
            capture_output=True, text=True, cwd=tmp, env=clean)
        checks.append(("in a fresh environment without mcp, the package "
                       "and the server module import, and import no mcp",
                       not has_mcp and imp.returncode == 0
                       and imp.stdout.strip().endswith("False"),
                       f"mcp installed {has_mcp}; {imp.stdout.strip()} "
                       f"{imp.stderr.strip()[-300:]}"))
        script = venv / "bin" / "spatialdds-mcp"
        if script.is_file():
            # stdin closed: were the SDK there after all, a stdio server
            # would read EOF and stop rather than wait for a client.
            try:
                run = subprocess.run([str(script), "--scene",
                                      "https://demo.spatialdds.org"],
                                     capture_output=True, text=True,
                                     stdin=subprocess.DEVNULL, timeout=60,
                                     env=clean)
                said = (run.returncode == 2
                        and 'pip install "spatialdds[mcp]"' in run.stderr,
                        f"exit {run.returncode}: "
                        f"{run.stderr.strip()[-200:]}")
            except subprocess.TimeoutExpired:
                said = (False, "it started and kept running: a server, "
                               "not a refusal")
        else:
            said = (False, f"no {script.name} in the environment's bin")
        checks.append(("…and `spatialdds-mcp` exits 2 naming the line to run",
                       *said))
        lib = subprocess.run([str(py), str(ROOT / "gates" / "library.py")],
                             capture_output=True, text=True, cwd=ROOT,
                             env={**clean, "PYTHONPATH": str(ROOT)},
                             timeout=600)
        tail = [l for l in lib.stdout.splitlines() if l.strip()][-1:] or [""]
        checks.append(("…and the client package's own gate passes there "
                       "(gates/library.py)", lib.returncode == 0,
                       f"exit {lib.returncode}: {tail[0]} "
                       f"{lib.stderr.strip()[-300:]}"))


# ---- B. the rule -----------------------------------------------------------

def static_imports() -> list[str]:
    out = []
    for n in ast.walk(ast.parse(MODULE.read_text())):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(("." * n.level) + (n.module or ""))
    return out


RUNTIME = r"""
import asyncio, json, sys
from spatialdds import mcp_server
from mcp import Client
view = mcp_server.View(mcp_server.Catalog([sys.argv[1]]))
view.start()
async def go():
    async with Client(mcp_server.build(view)) as c:
        for t in ("describe_scene", "get_claims", "get_entities"):
            await c.call_tool(t, {"scene": "ut-austin-littlefield"})
asyncio.run(go())
print(json.dumps(sorted(m for m in sys.modules)))
"""


def the_rule(checks: list, venue: _mcp.Venue) -> None:
    mods = static_imports()
    own = {m for m in mods if m.startswith(".")}
    bad = sorted({m for m in mods if m.split(".")[0] in
                  ("cyclonedds", "aiohttp", "mcap", "jsonschema")
                  or m.startswith(("spatialdds.server", "spatialdds.dds"))}
                 | (own - OWN_ALLOWED))
    checks.append(("the server's import statements name no DDS, no gateway "
                   "and no server dependency, and from its own package only "
                   "the client half", not bad and own <= OWN_ALLOWED,
                   f"imports {bad}"))
    r = subprocess.run([sys.executable, "-c", RUNTIME, venue.base],
                       capture_output=True, text=True, env=_mcp.server_env(),
                       timeout=120)
    try:
        loaded = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checks.append(("after three tool calls, nothing of DDS or the gateway "
                       "is loaded", False, r.stderr[-400:]))
        return
    hit = sorted(m for m in loaded if m.startswith(FORBIDDEN))
    checks.append(("after three tool calls, nothing of DDS, the gateway or "
                   "a server dependency is loaded (sys.modules)", not hit,
                   f"loaded {hit[:6]}"))


# ---- C. what it says -------------------------------------------------------

def claims_of(doc: dict) -> dict:
    return {c["claim"]["id"]: c for c in doc.get("claims", [])}


async def drained(venue: _mcp.Venue) -> None:
    await asyncio.sleep(0.8)


async def behaviours(checks: list, venue: _mcp.Venue) -> None:
    # Two writers the venue's copy does not have: a camera that crashes,
    # and a surveyor that withdraws. Written before the server starts, so
    # they are latched state its watch replays.
    t = int(time.time())
    seed = json.loads(json.dumps(next(iter(venue.records(CLAIMS).values()))))

    def claim(cid, asserter, basis, subj, obj, stamp, **kw):
        return {**seed, "id": cid, "asserter": asserter, "basis": basis,
                "subject_ref": subj, "object_ref": obj,
                "predicate": "http://www.opengis.net/ont/geosparql#sfWithin",
                "stamp": {"sec": str(stamp), "nanosec": 0},
                "state": "CREATED", "state_reason": "", "derived_from": [],
                "has_valid_from": False, "has_valid_until": False, **kw}
    cam = claim("gate:cam", "svc:camera:gate/north", "OBSERVED",
                "ent:duck:west", "ent:shallows:littlefield", t - 100)
    survey = claim("gate:survey", "svc:survey:gate/team", "DECLARED",
                   "ent:pond:littlefield", "ent:fountain:littlefield",
                   t + 3600)
    venue.write(CLAIMS, cam)
    venue.write(CLAIMS, survey)
    before = venue.records(CLAIMS)["gate:cam"]

    async with _mcp.stdio(["--scene", venue.base]) as c:
        tools = (await c.list_tools()).tools
        checks.append(("tools/list is the five tools, in order, each "
                       "read-only", tuple(t.name for t in tools)
                       == _mcp_tools() and all(
                           t.annotations and t.annotations.read_only_hint
                           for t in tools), str([t.name for t in tools])))
        ok = _mcp.wait_for(lambda: venue.subscribers(CLAIMS) >= 1
                           and venue.subscribers("entity") >= 1)
        await asyncio.sleep(0.5)
        checks.append(("the server subscribes to both public world lanes at "
                       "startup, before any call", ok, "no subscription"))

        async def call(tool, **args):
            r = await c.call_tool(tool, args)
            return r, _mcp.doc_of(r) if not r.is_error else {}

        _r, d = await call("describe_scene", scene=SCENE)
        lanes = {l["type"]: l for l in d["lanes"]}
        cmd = lanes["model_command"]
        read_cmd = [x for x in venue.requests if "model_command" in x[1]]
        checks.append(("an operator-scoped lane is described, not read: no "
                       "count, a note, and no request to it",
                       cmd["records"] is None and "token" in
                       cmd["records_note"] and not read_cmd,
                       f"{cmd}; requests {read_cmd}"))
        checks.append(("describe_scene names each lane's type as the "
                       "binding states it, and its schema",
                       lanes["entity"]["fqn"] == "spatial.owm.Entity"
                       and lanes[CLAIMS]["fqn"] == "oarc_owm.Relationship"
                       and lanes[CLAIMS]["schema"]
                       and lanes[CLAIMS]["schema"]["digest"]
                       .startswith("sha256:"), json.dumps(lanes[CLAIMS])))

        _r, d = await call("get_claims", scene=SCENE)
        base = claims_of(d)
        n0 = len(base)
        cam_row = base["gate:cam"]
        checks.append(("a replayed claim is not evidence of a live writer: "
                       "`unreported`", cam_row["liveliness"] == "unreported",
                       cam_row["liveliness"]))
        age = cam_row["age_s"]
        checks.append(("age is now minus the stamp", 99 <= age <= 130,
                       f"age_s {age} for a stamp 100 s before the write"))
        fut = base["gate:survey"]
        checks.append(("a stamp in the future is an age below zero, said so",
                       fut["age_s"] < -3000 and "in the future" in
                       fut["rendering"], fut["rendering"]))

        # Verbatim: every claim is the binding's record, member for member.
        served = venue.records(CLAIMS)
        diff = [k for k, row in base.items() if row["claim"] != served.get(k)]
        checks.append(("every claim is the binding's record, verbatim, "
                       "under `claim`", not diff and n0 == len(served),
                       f"differ: {diff[:3]}; {n0} returned, {len(served)} "
                       f"served"))

        # The camera's writer is lost.
        venue.liveliness(CLAIMS, "gate:cam", False)
        await drained(venue)
        _r, d = await call("get_claims", scene=SCENE)
        lost = claims_of(d).get("gate:cam", {})
        checks.append(("a lost writer's claim is kept and says so: "
                       "`lost`, \"nobody asserts this now\"",
                       lost.get("liveliness") == "lost"
                       and "nobody asserts this now" in lost.get("rendering",
                                                                 ""),
                       lost.get("rendering", "missing")))
        checks.append(("…and nothing was dropped",
                       len(claims_of(d)) == n0, f"{len(claims_of(d))} of {n0}"))
        unchanged = venue.records(CLAIMS)["gate:cam"] == before
        checks.append(("…while the binding's GET serves the record exactly "
                       "as before (finding 81: a poller is told nothing)",
                       unchanged, "the record changed"))

        # A reader that arrives after the loss is told nothing either.
        async with _mcp.stdio(["--scene", venue.base]) as late:
            await asyncio.sleep(1.5)
            r = await late.call_tool("get_claims", {"scene": SCENE})
            row = claims_of(_mcp.doc_of(r)).get("gate:cam", {})
            checks.append(("a server started after the loss reports it "
                           "`unreported`, as the binding leaves it "
                           "(finding 81)",
                           row.get("liveliness") == "unreported",
                           row.get("liveliness", "missing")))

        venue.liveliness(CLAIMS, "gate:cam", True)
        await drained(venue)
        _r, d = await call("get_claims", scene=SCENE)
        back = claims_of(d).get("gate:cam", {})
        checks.append(("an alive event is `reported`, and the line stops "
                       "saying nobody asserts it",
                       back.get("liveliness") == "reported"
                       and "nobody" not in back.get("rendering", ""),
                       back.get("rendering", "missing")))

        # The surveyor withdraws.
        venue.withdraw(CLAIMS, "gate:survey", "survey superseded")
        await drained(venue)
        _r, d = await call("get_claims", scene=SCENE)
        w = claims_of(d).get("gate:survey", {})
        checks.append(("a withdrawn claim is still returned, marked as no "
                       "longer served, with the binding's reason",
                       w.get("served") is False
                       and w["claim"].get("state") == "RETIRED"
                       and "survey superseded" in w.get("rendering", "")
                       and "no longer served" in w.get("rendering", ""),
                       w.get("rendering", "missing")))
        gone = "gate:survey" not in venue.records(CLAIMS)
        checks.append(("…which the binding no longer serves", gone,
                       "still served"))

        # Two asserters, one proposition.
        rival = claim("gate:rival", "svc:camera:gate/south", "OBSERVED",
                      "ent:duck:west", "ent:shallows:littlefield", t,
                      has_confidence=True, confidence=0.25)
        venue.write(CLAIMS, rival)
        await drained(venue)
        _r, d = await call("get_claims", scene=SCENE, subject="ent:duck:west")
        rows = d["claims"]
        prop = ["ent:duck:west",
                "http://www.opengis.net/ont/geosparql#sfWithin",
                "ent:shallows:littlefield"]
        idx = [i for i, c in enumerate(rows) if c["proposition"] == prop]
        asserters = [rows[i]["claim"]["asserter"] for i in idx]
        with urllib.request.urlopen(venue.base + f"/{SCENE}/world/"
                                    f"{CLAIMS}/v1", timeout=15) as resp:
            binding_order = [v["id"] for v in json.loads(resp.read())
                             if [v["subject_ref"], v["predicate"],
                                 v["object_ref"]] == prop]
        checks.append(("claims about one proposition come back side by "
                       "side, each its own claim, in the binding's order",
                       len(idx) == 3 and idx == list(range(idx[0],
                                                           idx[0] + 3))
                       and [rows[i]["claim"]["id"] for i in idx]
                       == binding_order,
                       f"asserters {asserters}, rows {idx}, binding "
                       f"{binding_order}"))

        # Filters.
        _r, d = await call("get_claims", scene=SCENE, predicate="P361")
        checks.append(("predicate matches a URI's last segment",
                       d["count"] >= 2 and all(
                           c["claim"]["predicate"].endswith("/P361")
                           for c in d["claims"]), str(d["count"])))
        _r, d = await call("get_entities", scene=SCENE, type="Q851478")
        checks.append(("type matches a type URI's last segment: the ducks",
                       sorted(e["entity_id"] for e in d["entities"])
                       == ["ent:duck:catalog-pose", "ent:duck:east",
                           "ent:duck:west"], str(d.get("count"))))

        # Near: to the box where there is one, to the pose where not.
        _r, d = await call("get_entities", scene=SCENE,
                           near={"x": 0.0, "y": -12.0, "radius_m": 0.5})
        rows = {e["entity_id"]: e for e in d["entities"]}
        checks.append(("near measures to an entity's box: a point inside "
                       "the pond's box but 12 m from its pose is 0 m away",
                       rows.get("ent:pond:littlefield", {}).get("distance_m")
                       == 0 and rows["ent:pond:littlefield"]["measured_to"]
                       == "extent" and "ent:duck:west" not in rows,
                       str(sorted(rows))))
        _r, d = await call("get_entities", scene=SCENE,
                           near={"x": 10.588685931520773,
                                 "y": -12.404398000892868, "radius_m": 0.01})
        rows = {e["entity_id"]: e for e in d["entities"]}
        duck = rows.get("ent:duck:west", {})
        checks.append(("…and to an entity's pose where it has no box",
                       duck.get("measured_to") == "pose"
                       and duck.get("distance_m") == 0.0, str(duck)))

        # Never silently left out.
        r, _d = await call("get_claims", scene="nowhere")
        checks.append(("an unknown scene is an error naming the scenes there "
                       "are", r.is_error and SCENE in _mcp.text_of(r),
                       _mcp.text_of(r)))

    # Both handshakes: a client of the 2026-07-28 protocol discovers, an
    # older one initializes. Claude Desktop and Claude Code are both kinds.
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "spatialdds.mcp_server",
                                      "--scene", venue.base],
        env=_mcp.server_env())
    async with Client(params, mode="legacy") as legacy:
        r = await legacy.call_tool("list_scenes", {})
        checks.append(("a client on the initialize handshake (legacy mode) "
                       "lists the scenes too", not r.is_error and SCENE in
                       _mcp.text_of(r), _mcp.text_of(r)[:200]))

    dead = _mcp.free_port()
    async with _mcp.stdio(["--scene", venue.base, "--scene",
                           f"http://127.0.0.1:{dead}"]) as c:
        r = await c.call_tool("list_scenes", {})
        d = _mcp.doc_of(r)
        checks.append(("an endpoint that cannot be read is listed as "
                       "unreadable, with the reason, beside the ones that "
                       "can", any(f"127.0.0.1:{dead}" in u["source"]
                                  for u in d.get("unreadable", []))
                       and d["count"] == 1, json.dumps(d.get("unreadable"))))


def _mcp_tools() -> tuple:
    from spatialdds import mcp_server
    return mcp_server.TOOLS


# ---- D. HTTP ---------------------------------------------------------------

def post(url: str, body: dict, headers: dict | None = None
         ) -> tuple[int, dict]:
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream",
         "MCP-Protocol-Version": "2025-06-18", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


PING = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}


async def over_http(checks: list, venue: _mcp.Venue) -> None:
    from spatialdds import mcp_server
    srv = _mcp.HttpServer(["--throttle", "0", "--scene", venue.base])
    try:
        async with _mcp.http(srv.url) as c:
            ok = []
            for tool, args in (("list_scenes", {}),
                               ("describe_scene", {"scene": SCENE}),
                               ("get_entities", {"scene": SCENE}),
                               ("get_claims", {"scene": SCENE}),
                               ("get_entity", {"scene": SCENE,
                                               "id": "ent:pond:littlefield"})):
                r = await c.call_tool(tool, args)
                ok.append((tool, not r.is_error and bool(_mcp.text_of(r))))
        checks.append(("the five tools answer over streamable HTTP",
                       all(o for _t, o in ok), str(ok)))
        st, _h = post(srv.url, PING, {"Host": "demo.spatialdds.org"})
        checks.append(("a request naming a host the server was not told to "
                       "answer to is refused (DNS rebinding)",
                       not 200 <= st < 300, f"status {st}"))
    finally:
        srv.stop()

    # One token every five seconds, in bursts of one: nothing refills
    # while the checks below run, so a bucket shared by every client
    # (the plant `front-is-one-client`) refuses the second client too.
    srv = _mcp.HttpServer(["--throttle", "0.2", "--allow-host",
                           "demo.spatialdds.org", "--scene", venue.base])
    try:
        # Each request below comes from a client of its own, as Caddy
        # forwards them, so each check spends its own bucket.
        st, _h = post(srv.url, PING, {"Host": "demo.spatialdds.org",
                                      "X-Forwarded-For": "192.0.2.1"})
        checks.append(("…and answered once it is allowed with --allow-host",
                       200 <= st < 300, f"status {st}"))
        got = [post(srv.url, PING, {"X-Forwarded-For": "203.0.113.9"})
               for _ in range(8)]
        refused = [(s, h) for s, h in got if s == 429]
        checks.append(("--throttle refuses a burst from one client with "
                       "429 and Retry-After",
                       refused and all("Retry-After" in h or "retry-after"
                                       in h for _s, h in refused),
                       f"statuses {[s for s, _h in got]}"))
        other = post(srv.url, PING, {"X-Forwarded-For": "198.51.100.4"})[0]
        checks.append(("…counting each forwarded client on its own, as "
                       "behind a TLS front", 200 <= other < 300,
                       f"another client's first request: {other}"))
    finally:
        srv.stop()
    text = GUIDE.read_text()
    src = MODULE.read_text()
    default = re.search(r'"--throttle", type=float, default=([\d.]+)', src)
    factor = re.search(r"Throttle\(app, rate, max\(1\.0, rate \* (\d+)\)\)",
                       src)
    words = {"five": 5, "twenty": 20}
    said = re.search(r"to (\w+) requests a second, in bursts\s+of up to "
                     r"(\w+)", text)
    checks.append(("the guide's throttle sentence is the server's default",
                   bool(default and factor and said)
                   and float(default.group(1)) == words.get(said.group(1))
                   and float(default.group(1)) * int(factor.group(1))
                   == words.get(said.group(2)),
                   f"default {default and default.group(1)}, factor "
                   f"{factor and factor.group(1)}, guide "
                   f"{said and said.groups()}"))


# ---- E. the transcripts ----------------------------------------------------

def transcripts(checks: list) -> None:
    sums = {}
    for line in (_mcp.FIXTURES / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    files = sorted(p.name for p in _mcp.FIXTURES.iterdir()
                   if p.name != "SHA256SUMS")
    bad = [n for n in files if sums.get(n) != hashlib.sha256(
        (_mcp.FIXTURES / n).read_bytes()).hexdigest()]
    checks.append(("every fixture in samples/mcp matches SHA256SUMS, and "
                   "SHA256SUMS lists every fixture",
                   not bad and sorted(sums) == files,
                   f"mismatched {bad}; listed {sorted(sums)}"))
    from spatialdds import mcp_server
    for name, transport in (("stdio-sandbox", "stdio"),
                            ("stdio-venue-live", "stdio"),
                            ("http-local", "http")):
        path = _mcp.FIXTURES / f"transcript-{name}.jsonl"
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        head, msgs = lines[0], lines[1:]
        calls = {m["message"]["id"]: m["message"]["params"]["name"]
                 for m in msgs if m["from"] == "client"
                 and m.get("message", {}).get("method") == "tools/call"}
        answered = {m["message"]["id"]: m["message"]["result"]
                    for m in msgs if m["from"] == "server"
                    and "result" in m.get("message", {})
                    and m["message"]["id"] in calls}
        good = sorted({calls[i] for i, r in answered.items()
                       if not r.get("isError") and r.get("content")})
        checks.append((f"{name}: a real client called all five tools over "
                       f"{transport}, and each answered",
                       good == sorted(mcp_server.TOOLS)
                       and head["transport"] == transport
                       and head["client"].startswith("mcp ")
                       and ("--http" in head["server"])
                       == (transport == "http"),
                       f"answered {good}; {head['server']}"))
        raw = path.read_text()
        checks.append((f"{name}: no credential crossed the transport",
                       "Bearer" not in raw and "Authorization" not in raw,
                       "found one"))
        if name == "stdio-sandbox":
            checks.append((f"{name}: against the public sandbox",
                           "https://demo.spatialdds.org" in head["server"],
                           head["server"]))
            continue
        binding = next(m for m in msgs if m["from"] == "binding")
        served = {r["id"]: r for r in binding["body"]}
        cid = next(i for i, t in calls.items() if t == "get_claims")
        doc = answered[cid].get("structuredContent") or json.loads(
            answered[cid]["content"][1]["text"])
        rows = doc["claims"]
        same = [r for r in rows if r["claim"] == served.get(r["claim"]["id"])]
        kept = [r for r in same
                if r["claim"]["basis"] == served[r["claim"]["id"]]["basis"]
                and r["claim"]["asserter"]
                == served[r["claim"]["id"]]["asserter"]]
        checks.append((f"{name}: every claim it returned is the binding's "
                       f"record verbatim, basis and asserter included "
                       f"({len(rows)} claim(s), against the binding's own "
                       f"answer recorded beside it)",
                       rows and len(same) == len(rows) == len(kept),
                       f"{len(same)} of {len(rows)} identical"))


# ---- the gate --------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="ABCDE")
    a = ap.parse_args()
    checks: list = []
    # A and E need no `mcp`: the module imports without it, which is part
    # of what A holds. B, C and D run the server.
    if "A" in a.only:
        packaging(checks)
    if "E" in a.only:
        transcripts(checks)
    try:
        import mcp  # noqa: F401
    except ImportError:
        if set(a.only) & set("BCD"):
            checks.append(("the server's behaviour (B, C, D)", SKIP,
                           'skipped, not failed: needs the [mcp] extra, '
                           'pip install "spatialdds[mcp]"'))
        return report("spatialdds-mcp", checks,
                      "packaging and transcripts only")
    if set(a.only) & set("BCD"):
        venue = _mcp.Venue()
        try:
            if "B" in a.only:
                the_rule(checks, venue)
            if "C" in a.only:
                asyncio.run(behaviours(checks, venue))
            if "D" in a.only:
                asyncio.run(over_http(checks, venue))
        finally:
            venue.stop()
        creds = [r for r in venue.requests if r[2]]
        checks.append(("no request the server made carried a credential",
                       not creds and bool(venue.requests),
                       f"{len(creds)} of {len(venue.requests)}: {creds[:3]}"))
    return report("spatialdds-mcp", checks,
                  "an extra that changes nothing else, reading as a "
                  "stranger and saying who said so")


if __name__ == "__main__":
    sys.exit(main())
