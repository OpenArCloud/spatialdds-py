#!/usr/bin/env python3
"""The integrator guide, executed: text shown to integrators is text that ran.

docs/integrator-guide.md is the public document an integrator follows against
demo.spatialdds.org. This gate runs it the way a fresh integrator does, in
document order, against a registry started here with the sandbox's own serve
line (`--demo --publish --authority demo.spatialdds.org --journal --tokens
--extension sandbox_owm02`, the module in deploy/extensions/), and fails on
drift either way: a snippet that no longer runs, or a response that no longer
matches the one the guide prints.

The guide carries markers, HTML comments that render as nothing, each on a
line of its own and bound to the fence that follows it:

    <!-- guide: install -->        the pip line; checked, not run (below)
    <!-- guide: setup -->          the export block; checked, not run (below)
    <!-- guide: sh -->             a ```sh fence, run by bash; the next
                                   ```console fence is its stdout, exactly
    <!-- guide: sh quiet -->       the same, and it prints nothing
    <!-- guide: python -->         a ```python fence, run by python3; the next
                                   ```console fence is its stdout
    <!-- guide: file NAME -->      the fence is saved as NAME, as the guide asks
    <!-- guide: start NAME -->     a ```sh fence started and left running, as
                                   the reader does in a second terminal
    <!-- guide: stop NAME -->      the ```console fence is everything NAME
                                   printed; it is stopped once it goes quiet
    <!-- guide: errors -->         the table after it: every row reproduced

What differs from a reader's run, and only that:

* **The base URL.** The setup block exports `BASE=https://demo.spatialdds.org`
  and two placeholders. It is checked to say exactly that, and every other
  block runs with `BASE` pointing at the local registry, `SCENE=my-world`,
  and a token issued here whose principal is `my-group`, the names the guide
  says its outputs were printed with. No block's text is altered.
* **The install.** The pip line is checked to pin the version this checkout's
  package declares, and every `spatialdds` version the prose names is that
  version too. The package itself is this checkout's, on PYTHONPATH, so the
  gate tests the code beside the document rather than a download.

Every block runs in one working directory (files persist, as in a reader's
terminal) and a fresh shell (variables do not, beyond the three the guide
asks a reader to set). Every runnable fence must carry a marker: a snippet the
gate does not run is reported, because it is one a reader runs untested.

**The error table.** Each row's status, type and title are compared with the
problem document a request built for that row draws from the registry. A row
with no reproduction fails, and so does a reproduction with no row. The disk
row is drawn from a second registry whose journal is on a 4 MiB volume of its
own, filled before it starts (a disk image on macOS, a tmpfs in a user
namespace on Linux); where neither can be had, that row fails and says so.

Every registry started here must be the process answering on its port, by
its pid on the answering connection (harness.own_server, finding 77), before
anything it says is compared.

**The known issue.** The guide says a refused write to a lane that does not
exist yet still opens it (finding 73). That is checked as a claim like any
other: when the fix lands, this fails until the paragraph goes.

Needs the network for the snippets that fetch the sample asset from GitHub,
as a reader's do; with it unreachable the gate FAILS and says so, and
gates/run_all.py turns that into a stated skip.

    gates/integrator_guide.py            run the guide and compare
    gates/integrator_guide.py --write    run it and write the outputs into
                                         the guide (for review, then commit)
    gates/integrator_guide.py --live     the read-only blocks against the
                                         live sandbox; not part of run_all

Usage:  gates/integrator_guide.py [--write | --live]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "spatialdds"
EXT = ROOT / "deploy" / "extensions"
GUIDE = ROOT / "docs" / "integrator-guide.md"
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(LIB))

from harness import own_server, report                       # noqa: E402

LIVE = "https://demo.spatialdds.org"
SCENE, PRINCIPAL = "my-world", "my-group"
#: The setup block, exactly: the one block the gate reads rather than runs.
SETUP = ("export BASE=https://demo.spatialdds.org\n"
         "export SCENE='<your scene>'\n"
         "export SPATIALDDS_TOKEN='<your token>'")
MARKER = re.compile(r"^<!--\s*guide:\s*(.+?)\s*-->$")
FENCE = re.compile(r"^```(\w*)\s*$")
ASSET = ("https://raw.githubusercontent.com/OpenArCloud/spatialdds-web/main/"
         "samples/assets/fountain-marker.gltf")


def version() -> str:
    text = (LIB / "spatialdds" / "__init__.py").read_text()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


# ---- the document ----------------------------------------------------------

class Block:
    """One marker and the fence it binds, plus its expected output if any."""

    def __init__(self, kind: str, arg: str, line: int, lang: str,
                 body: str) -> None:
        self.kind, self.arg, self.line = kind, arg, line
        self.lang, self.body = lang, body
        self.expected: str | None = None
        #: (start, end) line indices of the console fence's body, for --write.
        self.out_span: tuple[int, int] | None = None


def parse(lines: list[str]) -> tuple[list[Block], list[int], list[str]]:
    """Blocks in document order, the lines of unmarked runnable fences, and
    the error table's rows."""
    blocks: list[Block] = []
    unmarked: list[int] = []
    table: list[str] = []
    i, pending = 0, None
    while i < len(lines):
        m = MARKER.match(lines[i])
        if m:
            kind, _, arg = m.group(1).partition(" ")
            pending = (kind, arg.strip(), i + 1)
            if kind == "errors":
                j = i + 1
                while j < len(lines) and lines[j].startswith("|"):
                    table.append(lines[j])
                    j += 1
                pending = None
                i = j
                continue
            i += 1
            continue
        f = FENCE.match(lines[i])
        if f:
            lang = f.group(1)
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            body = "\n".join(lines[i + 1:j])
            if lang == "console":
                owner = pending if pending and pending[0] == "stop" else None
                if owner:
                    b = Block("stop", owner[1], owner[2], lang, "")
                    b.expected, b.out_span = body, (i + 1, j)
                    blocks.append(b)
                    pending = None
                elif blocks and blocks[-1].expected is None \
                        and blocks[-1].kind in ("sh", "python"):
                    blocks[-1].expected, blocks[-1].out_span = body, (i + 1, j)
            elif pending:
                blocks.append(Block(pending[0], pending[1], pending[2],
                                    lang, body))
                pending = None
            elif lang in ("sh", "python", "bash"):
                unmarked.append(i + 1)
            i = j + 1
            continue
        i += 1
    return blocks, unmarked, table


# ---- the registry ----------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def issue(table: Path, name: str, provider: str, scene: str,
          max_records: int) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "spatialdds.tokens", "--file", str(table),
         "add", name, "--provider", provider, "--scene", scene,
         "--may-create-scenes", "--max-records", str(max_records)],
        env=dict(os.environ, PYTHONPATH=str(LIB)), capture_output=True,
        text=True, check=True)
    return out.stdout.strip().splitlines()[-1]


class Registry:
    """`python -m spatialdds.serve` with the sandbox's flags, on a free port.

    `journal_dir` puts the journal somewhere else (a full volume, for the
    503 row), and `prefix` is argv run in front of the server, which execs
    it, so the started pid is still the server's.
    """

    def __init__(self, tmp: Path, journal_dir: Path | None = None,
                 prefix: tuple[str, ...] = ()) -> None:
        self.tmp = tmp
        #: The 503 row's side claims, once reproduce() has drawn it.
        self.full: tuple[bool, str] = (False, "not drawn")
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        table = tmp / "tokens.json"
        # The sandbox's lane quota for the guide's principal; a second
        # principal with a quota of 2 reproduces the quota row without 500
        # writes.
        self.token = issue(table, PRINCIPAL, "example.org", SCENE, 500)
        self.other = issue(table, "other", "other.example", "other-world", 2)
        self.log = open(tmp / "serve.log", "w")
        journal = (journal_dir or tmp) / "registry.mcap"
        self.proc = subprocess.Popen(
            [*prefix, sys.executable, "-m", "spatialdds.serve", "--demo",
             "--publish", "--authority", "demo.spatialdds.org", "--host",
             "127.0.0.1", "--port", str(self.port), "--journal", str(journal),
             "--tokens", str(table), "--extension", "sandbox_owm02"],
            cwd=tmp, stdout=self.log, stderr=subprocess.STDOUT,
            env=dict(os.environ,
                     PYTHONPATH=os.pathsep.join([str(LIB), str(EXT)])))
        # The started process must be the one answering (finding 77): a free
        # port can be taken between the probe and the bind.
        ours, who = own_server(self.proc, self.port, timeout=60)
        if not ours:
            self.stop()
            raise RuntimeError(f"the registry is not the one answering "
                               f"({who}):\n"
                               + (tmp / "serve.log").read_text()[-2000:])

    def banner(self) -> str:
        return (self.tmp / "serve.log").read_text()

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


# ---- running the blocks ----------------------------------------------------

def reader_env(base: str, token: str | None) -> dict:
    """A reader's shell: theirs, plus the three variables the guide sets."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("BASE", "SCENE", "SPATIALDDS_TOKEN")}
    env["BASE"] = base
    if token is not None:
        env["SCENE"], env["SPATIALDDS_TOKEN"] = SCENE, token
    # `pip install spatialdds`, as this checkout: see the module docstring.
    env["PYTHONPATH"] = str(LIB)
    env["PYTHONWARNINGS"] = "ignore"
    return env


def run_block(b: Block, work: Path, env: dict) -> tuple[int, str, str]:
    if b.kind in ("sh",):
        argv = ["bash", "-c", b.body]
    else:
        argv = [sys.executable, "-"]
    r = subprocess.run(argv, cwd=work, env=env, capture_output=True,
                       text=True, input=b.body if b.kind == "python" else None,
                       timeout=120)
    return r.returncode, r.stdout, r.stderr


class Watcher:
    def __init__(self, b: Block, work: Path, env: dict) -> None:
        self.path = work / f".{b.arg}.out"
        self.f = open(self.path, "w")
        self.proc = subprocess.Popen(["bash", "-c", b.body], cwd=work,
                                     env=env, stdout=self.f,
                                     stderr=subprocess.STDOUT,
                                     start_new_session=True)
        # A reader starts it and turns to the other terminal once it has
        # said something: the replay marker, at least.
        for _ in range(60):
            if "end_of_replay" in self.path.read_text():
                break
            time.sleep(0.25)

    def stop(self) -> str:
        last, still = None, 0
        for _ in range(80):
            now = self.path.read_text()
            still = still + 1 if now == last else 0
            if still >= 6:
                break
            last = now
            time.sleep(0.25)
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, 15)
            self.proc.wait(timeout=10)
        self.f.close()
        return self.path.read_text()


def norm(text: str) -> str:
    return "\n".join(l.rstrip() for l in text.strip("\n").splitlines())


# ---- the error table -------------------------------------------------------

def http(method: str, url: str, token: str | None = None,
         body: bytes | None = None, ctype: str | None = "application/json",
         headers: dict | None = None) -> tuple[int, dict, dict]:
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    if body is not None and ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, hdrs, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, dict(e.headers), e.read()
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = {"raw": raw[:200].decode("utf-8", "replace")}
    return status, hdrs, doc


def entity(key: str, basis: str = "DECLARED") -> dict:
    return {"entity_id": key, "basis": basis, "type_uris": [],
            "layer": "FAST",
            "frame_ref": {"uuid": "", "fqn": f"map/{SCENE}",
                          "has_coord_convention": True,
                          "coord_convention": "ENU", "has_scale": True,
                          "scale_status": "SCALE_DECLARED",
                          "meters_per_unit": 1.0, "display_unit": "m"},
            "has_pose": True,
            "pose": {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]},
            "has_extent": False,
            "extent": {"min_xyz": [0.0, 0.0, 0.0],
                       "max_xyz": [0.0, 0.0, 0.0]},
            "properties": [], "external_refs": [], "content_refs": [],
            "state": "ACTIVE", "state_reason": "", "source_id": "gate",
            "stamp": {"sec": "1793300000", "nanosec": 0}}


def claim(key: str, asserter: str = "") -> dict:
    return {"id": key, "subject_ref": "ent:a",
            "predicate": "https://example.org/predicate/near",
            "object_ref": "ent:b", "basis": "DECLARED", "asserter": asserter,
            "has_confidence": False, "confidence": 0.0, "derived_from": [],
            "stamp": {"sec": "1793300000", "nanosec": 0},
            "has_valid_from": False,
            "valid_from": {"sec": "0", "nanosec": 0},
            "has_valid_until": False,
            "valid_until": {"sec": "0", "nanosec": 0},
            "meta": [], "state": "CREATED", "state_reason": ""}


def enc(d: dict) -> bytes:
    return json.dumps(d).encode()


def rate_limited(reg: Registry) -> list[tuple[int, dict, dict]]:
    """Requests as the deployed TLS front forwards them, until one is refused.

    A loopback peer with no forwarded address is the operator and is not
    limited (server/limits.py); behind Caddy every outside request arrives
    with X-Forwarded-For, so that is what an outside client is here.
    """
    import concurrent.futures as cf
    url = reg.base + "/.well-known/spatialdds/search"
    fwd = {"X-Forwarded-For": "203.0.113.7"}
    with cf.ThreadPoolExecutor(32) as pool:
        got = list(pool.map(lambda _: http("GET", url, headers=fwd),
                            range(120)))
    return [g for g in got if g[0] == 429][:1]


async def subscriptions_full(reg: Registry) -> tuple[int, dict, dict]:
    """256 open streams, then one more: the cap's refusal, as a document."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus
    url = reg.base.replace("http", "ws", 1) + \
        "/ut-austin-south-mall/content/content_announce/v1"
    held = []
    try:
        for _ in range(256):
            held.append(await connect(url))
        try:
            extra = await connect(url)
            held.append(extra)
            return 101, {}, {}
        except InvalidStatus as e:
            r = e.response
            return r.status_code, dict(r.headers), json.loads(r.body or b"{}")
    finally:
        for c in held:
            await c.close()


#: Run in front of the server: fill the directory named as $0, then exec the
#: rest, so the volume is full before the server starts and the started pid
#: is the server's. Bounded (8 MiB, then 1024 small files) so that a volume
#: that is not the small one is never filled.
FILL = ('head -c 8388608 /dev/zero > "$0/fill" 2>/dev/null; i=0; '
        'while [ $i -lt 1024 ] && head -c 4096 /dev/zero > "$0/f$i" '
        '2>/dev/null; do i=$((i+1)); done; exec "$@"')


@contextlib.contextmanager
def full_volume(tmp: Path):
    """A 4 MiB filesystem of its own, for a journal: yields (dir, prefix).

    Unprivileged on both platforms the gate runs on: a disk image on macOS,
    a tmpfs in a user and mount namespace on Linux (the server runs inside
    it, so it is filled there too, by FILL).
    """
    vol = tmp / "vol"
    vol.mkdir()
    if sys.platform == "darwin":
        img = tmp / "vol.dmg"
        subprocess.run(["hdiutil", "create", "-size", "4m", "-fs", "HFS+",
                        "-volname", "sdd-full", "-layout", "NONE", str(img)],
                       check=True, capture_output=True)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-noautoopen",
                        "-mountpoint", str(vol), str(img)],
                       check=True, capture_output=True)
        try:
            yield vol, ("sh", "-c", FILL, str(vol))
        finally:
            subprocess.run(["hdiutil", "detach", "-force", str(vol)],
                           capture_output=True)
    elif sys.platform.startswith("linux"):
        yield vol, ("unshare", "--user", "--map-root-user", "--mount",
                    "sh", "-c",
                    'mount -t tmpfs -o size=4m tmpfs "$0" && ' + FILL,
                    str(vol))
    else:
        raise RuntimeError(f"no unprivileged small volume on {sys.platform}")


def journal_unwritable(tmp: Path) -> tuple[list[tuple[int, dict, dict]],
                                           tuple[bool, str]]:
    """A write to a registry whose journal's disk is full.

    A registry of its own, with the sandbox's flags and its journal on a
    full volume. Returns the refusal, and whether what the row says beside
    it holds: the disk was the cause, nothing was stored, reads still work.
    """
    (tmp / "full").mkdir()
    try:
        with full_volume(tmp / "full") as (vol, prefix):
            reg = Registry(tmp / "full", journal_dir=vol, prefix=prefix)
            try:
                url = f"{reg.base}/{SCENE}/world/entity/v1/ent:full"
                got = http("PUT", url, reg.token, enc(entity("ent:full")))
                stored = http("GET", url, reg.token)[0]
                reads = http("GET",
                             reg.base + "/.well-known/spatialdds/search")[0]
            finally:
                reg.stop()
    except (RuntimeError, subprocess.CalledProcessError) as e:
        # No small volume here (Linux with unprivileged user namespaces
        # refused, say): the row has no reproduction, and says why.
        return [], (False, f"no full volume to draw it from: "
                           f"{str(e).strip()[-300:]}")
    detail = str(got[2].get("detail", ""))
    ok = ("No space left" in detail and got[1].get("Retry-After") == "60"
          and stored == 404 and reads == 200)
    return [got], (ok, f"detail {detail[:120]!r}, Retry-After "
                       f"{got[1].get('Retry-After')}, stored {stored}, "
                       f"reads {reads}")


def reproduce(reg: Registry) -> dict[str, list[tuple[int, dict, dict]]]:
    """slug -> every response drawn for that row (each must match it)."""
    b, t, o = reg.base, reg.token, reg.other
    ent = f"{b}/{SCENE}/world/entity/v1"
    rel = f"{b}/{SCENE}/world/oarc.relationship/v1"
    big = enc({**entity("ent:big"), "pad": "x" * (256 * 1024)})
    rows: dict[str, list] = {
        "schema-invalid": [http("PUT", f"{ent}/ent:x", t,
                                enc({"entity_id": "ent:x"}))],
        "not-json": [http("PUT", f"{ent}/ent:x", t, b"nope")],
        "bad-key": [http("PUT", f"{ent}/ent:x%01", t,
                         enc(entity("ent:x\u0001")))],
        "key-mismatch": [http("PUT", f"{ent}/ent:y", t, enc(entity("ent:x")))],
        "refresh-required": [
            http("PUT", f"{ent}/ent:o", t, enc(entity("ent:o", "OBSERVED"))),
            http("PUT", f"{rel}/c-o", t,
                 enc({**claim("c-o"), "basis": "OBSERVED"}))],
        "refresh-not-applicable": [
            http("PUT", f"{ent}/ent:d?refresh=5", t, enc(entity("ent:d"))),
            http("PUT", f"{rel}/c-d?refresh=5", t, enc(claim("c-d")))],
        "refresh-invalid": [http("PUT", f"{ent}/ent:o?refresh=0.1", t,
                                 enc(entity("ent:o", "OBSERVED")))],
        "operator-scoped": [
            http("GET", ent),
            http("GET", rel),
            http("PUT", f"{ent}/ent:x", None, enc(entity("ent:x"))),
            http("PUT", f"{ent}/ent:x", "a-token-nobody-issued",
                 enc(entity("ent:x")))],
        "not-your-lane": [http("PUT", f"{ent}/ent:x", o, enc(entity("ent:x")))],
        "quota-exceeded": [http("PUT", f"{b}/other-world/world/entity/v1/ent:{k}",
                                o, enc(entity(f"ent:{k}")))
                           for k in ("q1", "q2", "q3")][2:],
        "asserter-not-principal": [http("PUT", f"{rel}/c-f", t,
                                        enc(claim("c-f", "somebody-else")))],
        "no-such-instance": [http("GET", f"{ent}/ent:nobody", t)],
        "no-such-manifest": [http(
            "GET", f"{b}/spatialdds/resolve?uri=spatialdds%3A%2F%2F"
                   f"demo.spatialdds.org%2F{SCENE}%2Fcontent%2Fnobody")],
        "no-route": [http("PUT", ent, t, enc(entity("ent:x")))],
        "not-publishable": [http("PUT", f"{b}/{SCENE}/world/nosuch/v1/x", t,
                                 enc({}))],
        "body-too-large": [http("PUT", f"{ent}/ent:big", t, big)],
        "uri-too-long": [http("GET", f"{b}/{SCENE}/content/content_announce/v1/"
                                     + "k" * (16 * 1024))],
        "unsupported-media-type": [http("PUT", f"{ent}/ent:x", t,
                                        enc(entity("ent:x")), "text/plain")],
        "rate-limited": rate_limited(reg),
        "too-many-subscriptions": [asyncio.run(subscriptions_full(reg))],
    }
    rows["journal-unwritable"], reg.full = journal_unwritable(reg.tmp)
    return rows


def table_rows(lines: list[str]) -> list[tuple[int, str, str]]:
    rows = []
    for l in lines[2:]:
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        rows.append((int(cells[0]), cells[1].strip("`"), cells[2]))
    return rows


def lane_listed(reg: Registry, path: str) -> bool:
    _s, _h, doc = http("GET", reg.base + "/.well-known/spatialdds/search")
    return any(t["url"].endswith(path)
               for r in doc.get("results", []) for t in r["service"]["topics"])


# ---- the gate --------------------------------------------------------------

def reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"),
                               timeout=10)
        return True
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true")
    g.add_argument("--live", action="store_true")
    a = ap.parse_args()

    lines = GUIDE.read_text().split("\n")
    blocks, unmarked, table = parse(lines)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("every runnable fence carries a marker, so none goes untested",
                   not unmarked, f"unmarked fences at lines {unmarked}"))

    if a.live:
        return live(blocks, checks)

    if not reachable(ASSET):
        checks.append(("the sample asset is reachable, as it is for a reader",
                       False, f"{ASSET} is unreachable from here"))
        return report("the integrator guide, executed", checks,
                      "the guide fetches its sample asset from GitHub")

    v = version()
    install = [b for b in blocks if b.kind == "install"]
    checks.append((f"the install line pins this checkout's version ({v})",
                   len(install) == 1
                   and install[0].body.strip() == f'pip install "spatialdds=={v}"',
                   install[0].body if install else "no install block"))
    named = set(re.findall(r"spatialdds(?:`|==)\s?(\d+\.\d+\.\d+)",
                           GUIDE.read_text()))
    checks.append((f"every spatialdds version the prose names is {v}",
                   named == {v}, f"named: {sorted(named)}"))
    setup = [b for b in blocks if b.kind == "setup"]
    checks.append(("the setup block exports the sandbox's base and the two "
                   "placeholders, and nothing else",
                   len(setup) == 1 and setup[0].body.strip() == SETUP,
                   setup[0].body if setup else "no setup block"))

    # The open-file limit: 256 streams and their sockets, both ends.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = 4096 if hard == resource.RLIM_INFINITY else min(4096, hard)
    if soft < want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))

    tmp = Path(tempfile.mkdtemp(prefix="integrator-guide-"))
    work = tmp / "reader"
    work.mkdir()
    reg = Registry(tmp)
    try:
        checks.append(("the registry serves claims, as the sandbox's banner says",
                       "extension     oarc.relationship (oarc_owm.Relationship): "
                       "publishable" in reg.banner(), reg.banner()[-400:]))
        env = reader_env(reg.base, reg.token)
        watchers: dict[str, Watcher] = {}
        outputs: list[tuple[Block, str]] = []
        for b in blocks:
            where = f"line {b.line}"
            if b.kind in ("install", "setup", "errors"):
                continue
            if b.kind == "file":
                (work / b.arg).write_text(b.body + "\n")
                continue
            if b.kind == "start":
                watchers[b.arg] = Watcher(b, work, env)
                continue
            if b.kind == "stop":
                got = watchers.pop(b.arg).stop()
                outputs.append((b, got))
                checks.append((f"{where}: what `{b.arg}` printed is what the "
                               f"guide shows", norm(got) == norm(b.expected),
                               diff(b.expected, got)))
                continue
            code, out, err = run_block(b, work, env)
            outputs.append((b, out))
            quiet = b.arg == "quiet"
            if quiet:
                ok = code == 0 and out == "" and b.expected is None
            else:
                ok = code == 0 and b.expected is not None \
                    and norm(out) == norm(b.expected)
            first = b.body.strip().splitlines()[0][:60]
            checks.append((f"{where}: `{first}` runs and prints what the guide "
                           f"shows", ok,
                           f"exit {code}; {diff(b.expected or '', out)}"
                           + (f"; stderr: {err.strip()[-300:]}" if err.strip()
                              else "")))
        for name, w in watchers.items():
            w.stop()
            checks.append((f"watcher `{name}` is stopped where the guide stops "
                           f"it", False, "no stop marker"))

        if a.write:
            write(lines, outputs)
            print(f"wrote {len(outputs)} output(s) into {GUIDE.name}; review "
                  f"the diff, then run without --write")
            return 0

        # The error table, both directions.
        documented = table_rows(table)
        drawn = reproduce(reg)
        slugs = [r[1] for r in documented]
        checks.append(("every row of the error table has a reproduction, and "
                       "every reproduction a row",
                       sorted(slugs) == sorted(drawn) and len(set(slugs)) == len(slugs),
                       f"rows only: {sorted(set(slugs) - set(drawn))}; "
                       f"reproductions only: {sorted(set(drawn) - set(slugs))}"))
        for status, slug, title in documented:
            got = drawn.get(slug) or []
            seen = [(s, str(d.get("type", "")).rsplit(":", 1)[-1],
                     d.get("title")) for s, _h, d in got]
            ok = bool(seen) and all(x == (status, slug, title) for x in seen)
            if slug == "rate-limited":
                ok = ok and all("Retry-After" in h for _s, h, _d in got)
            checks.append((f"{status} {slug}: {title}", ok,
                           f"drew {seen or 'nothing'}"))
        checks.append(("…and the full disk was the cause of the 503, with "
                       "Retry-After, nothing stored, and reads unaffected, "
                       "as its row says", *reg.full))

        # The known issue, held as a claim: a refused PUT opens a lane.
        path = f"/{SCENE}/frames/geo_anchor/v1"
        before = lane_listed(reg, path)
        s, _h, _d = http("PUT", f"{reg.base}{path}/nothing", reg.token,
                         enc({"anchor_id": "nothing"}))
        after = lane_listed(reg, path)
        checks.append(("the known issue still holds: a PUT refused with 400 "
                       "opens a lane search lists (finding 73); when this "
                       "fails, the fix has landed and the guide's paragraph "
                       "goes", not before and s == 400 and after,
                       f"listed before {before}, status {s}, listed after {after}"))
    finally:
        reg.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    return report("the integrator guide, executed", checks,
                  "every snippet ran as printed, every refusal is the "
                  "documented one")


def diff(expected: str, got: str) -> str:
    e, g = norm(expected).splitlines(), norm(got).splitlines()
    for i in range(max(len(e), len(g))):
        x = e[i] if i < len(e) else "<nothing>"
        y = g[i] if i < len(g) else "<nothing>"
        if x != y:
            return f"first difference at output line {i + 1}: guide {x!r}, ran {y!r}"
    return "identical"


def write(lines: list[str], outputs: list[tuple[Block, str]]) -> None:
    """Each console fence's body replaced by what its block printed."""
    for b, out in sorted(outputs, key=lambda x: -(x[0].out_span or (0, 0))[0]):
        if b.out_span is None:
            continue
        s, e = b.out_span
        lines[s:e] = norm(out).splitlines()
    GUIDE.write_text("\n".join(lines))


def live(blocks: list[Block], checks: list) -> int:
    """The read-only blocks against the live sandbox: no token, no writes.

    Not part of run_all: it reads a deployment, which a gate of this
    repository's code has no business depending on. Run it on demand, as
    the conformance suite's live run is.
    """
    work = Path(tempfile.mkdtemp(prefix="integrator-guide-live-"))
    env = reader_env(LIVE, None)
    reads = [b for b in blocks if b.kind in ("sh", "python")
             and "$SCENE" not in b.body and "SPATIALDDS_TOKEN" not in b.body
             and "SCENE" not in b.body and "-X " not in b.body]
    for b in reads:
        code, out, err = run_block(b, work, env)
        first = b.body.strip().splitlines()[0][:60]
        checks.append((f"line {b.line}: `{first}` against {LIVE}",
                       code == 0 and norm(out) == norm(b.expected or ""),
                       f"exit {code}; {diff(b.expected or '', out)}"))
    shutil.rmtree(work, ignore_errors=True)
    return report("the integrator guide's read-only blocks, live", checks,
                  f"{len(reads)} read-only block(s) print against {LIVE} what "
                  f"the guide shows")


if __name__ == "__main__":
    sys.exit(main())
