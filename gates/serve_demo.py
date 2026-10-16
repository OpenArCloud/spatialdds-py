#!/usr/bin/env python3
"""`python -m spatialdds.serve --demo`, from a wheel's point of view.

The claim 0.2.0 makes is that `pip install` is enough, so this gate refuses to
believe anything a checkout makes easy. It starts the endpoint the way a
stranger would -- as a subprocess, in an empty working directory, with only
the package on `PYTHONPATH` and nothing of this repository -- and then runs
this repository's own examples against it unaltered.

That last part is the acceptance bar worth having: the examples were written
against `tools/run_gateway.py --mcap samples/fountain-anchors.mcap`, months
before the package existed. If they pass against `--demo` without a word
changed, then the packaged data really is the committed data and the server
really is the same server.

Two things it also checks, both of which have bitten:

**The packaged copy is the committed copy.** `spatialdds/demo/` exists because
package data must live inside the package; `samples/` is where the recording
is authored. `tools/pack_demo.py --check` compares them, and a stale packaged
recording would mean a demo that no longer matches its own golden vectors.

**A demo leaves nothing behind.** It publishes in memory, so the directory it
was started in is as empty afterwards as before.

Usage:  gates/serve_demo.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"
sys.path.insert(0, str(LIB))

from harness import report                                    # noqa: E402

import examples as ex                                          # noqa: E402
from quickstart import listening                               # noqa: E402

PACK = ROOT / "tools" / "pack_demo.py"
DEMO_DIR = LIB / "spatialdds" / "demo"


def get(url: str, token: str | None = None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read()


class Served:
    """`python -m spatialdds.serve` as a stranger runs it."""

    def __init__(self, *args: str) -> None:
        self.args = args
        self.port = int(args[args.index("--port") + 1])
        self.owner = ""
        self.proc: subprocess.Popen | None = None
        self.tmp: tempfile.TemporaryDirectory | None = None
        self.banner = ""
        self.token = ""
        self.base = ""
        self.cwd = Path()

    def __enter__(self) -> "Served":
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)
        # Only the package. No ROOT, so an import that reached back into this
        # repository would fail here rather than quietly work.
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(LIB)}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "spatialdds.serve", *self.args],
            cwd=str(self.cwd), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        # Two steps, because the banner is not a readiness signal: every
        # line of it is printed before the socket is bound. So read the
        # banner to its last line -- which is the `publish` line, always --
        # and then wait for the port to be owned by *this* child, using the
        # same lsof check `gates/quickstart.py` uses and importing it rather
        # than writing a second one. A URL that answers only tells you
        # something is listening there.
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            if line.startswith("  publish "):
                break
        self.banner = "".join(lines)
        for _ in range(300):
            if self.proc.poll() is not None:
                break
            owner = listening(self.port)
            if owner and owner.split()[1] == str(self.proc.pid):
                self.owner = owner
                break
            time.sleep(0.1)
        for ln in lines:
            if ln.startswith("bearer token"):
                self.token = ln.split()[-1]
            if ln.startswith("serving"):
                self.base = ln.split()[1]
        if "(bound" in self.banner:
            self.base = "http://" + self.banner.split("(bound ", 1)[1] \
                .split(")")[0]
        return self

    def __exit__(self, *exc) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:         # pragma: no cover
                self.proc.kill()
        if self.tmp is not None:
            self.tmp.cleanup()


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    r = subprocess.run([sys.executable, str(PACK), "--check"], cwd=str(ROOT),
                       capture_output=True, text=True)
    checks.append((f"the packaged demo set is the committed one "
                   f"({r.stdout.strip().splitlines()[-1] if r.stdout else '-'})",
                   r.returncode == 0, r.stdout.strip()[-200:]))
    size = sum(p.stat().st_size for p in DEMO_DIR.rglob("*") if p.is_file())
    print(f"  note spatialdds/demo/ is {size} bytes across "
          f"{len([p for p in DEMO_DIR.rglob('*') if p.is_file()])} file(s)")

    port = 8793
    with Served("--demo", "--port", str(port)) as s:
        checks.append((f"`python -m spatialdds.serve --demo` serves from an "
                       f"empty directory with only the package on the path, "
                       f"and the listening process is the one started",
                       bool(s.owner)
                       and s.owner.split()[1] == str(s.proc.pid)
                       and "publish     ON" in s.banner,
                       f"owner {s.owner[:60]!r}, pid {s.proc.pid}"))
        checks.append(("its banner names the type source and the spec commit "
                       "it was pinned at",
                       "types         the recording's own embedded IDL"
                       in s.banner and "SpatialDDS-spec" in s.banner,
                       f"{[l for l in s.banner.splitlines() if 'types' in l]}"))
        checks.append(("…and says which line serves resolution, instead of "
                       "leaving the packaged manifests as a 404 nobody "
                       "expected",
                       "--demo --authority demo.spatialdds.org" in s.banner,
                       "the note is missing"))
        base = f"http://127.0.0.1:{port}"
        st, body = get(f"{base}/.well-known/spatialdds/search?geohash=9v6krc")
        doc = json.loads(body)
        checks.append((f"the packaged recording is being served: search finds "
                       f"{len(doc['results'])} service(s)",
                       st == 200 and len(doc["results"]) == 1,
                       f"{st} {body[:120]!r}"))
        st, body = get(f"{base}/ut-austin-south-mall/frames/geo_anchor/v1")
        checks.append((f"with the recording's five anchors",
                       st == 200 and len(json.loads(body)) == 5,
                       f"{st}, {len(json.loads(body))} anchor(s)"))
        st, body = get(f"{base}/.well-known/spatialdds/schemas")
        index = json.loads(body)
        n = len(index if isinstance(index, list)
                else index.get("schemas", []))
        checks.append((f"and the {n} generated schemas, which travel in the "
                       f"wheel because §8.4's reader has nothing else to go "
                       f"on", st == 200 and n == 112, f"{st}, {n} schema(s)"))
        st, body = get(f"{base}/assets/duck.gltf")
        checks.append((f"the packaged assets are reachable "
                       f"({len(body)} bytes of duck)",
                       st == 200 and len(body) == 1432, f"{st} {len(body)}"))

        # The acceptance bar: this repository's own examples, unaltered,
        # against the packaged endpoint. Their documented output comes from
        # `gates/examples.py`'s parser, so there is one place that knows what
        # an example is supposed to print.
        blocks = [b for b in ex.parse(ROOT / "examples" / "README.md")
                  if b.mode == "py" and b.rec == "fountain"]
        ran = []
        for b in blocks:
            env = dict(os.environ, SPATIALDDS_ENDPOINT=base, BASE=base,
                       PYTHONPATH=os.pathsep.join([str(ROOT), str(LIB)]))
            p = subprocess.run([sys.executable, b.target], cwd=str(ROOT),
                               capture_output=True, text=True, env=env,
                               timeout=120)
            # `$BASE` and any loopback URL in the documented output get the
            # same treatment `gates/examples.py` gives them -- the expected
            # side needs it as much as the actual side, which is what the
            # first run of this gate got wrong.
            want = [ex.rebase(l.replace("$BASE", base), base).rstrip()
                    for l in b.expected]
            got = [l.rstrip() for l in ex.rebase(p.stdout, base).splitlines()]
            ran.append((b.target, p.returncode == 0 and got == want))
        checks.append((f"every fountain example in examples/README.md runs "
                       f"unaltered against --demo and prints what it "
                       f"documents ({len(ran)} example(s): "
                       f"{', '.join(Path(t).name for t, _ in ran)})",
                       bool(ran) and all(ok for _t, ok in ran),
                       f"{[t for t, ok in ran if not ok]}"))
        left = sorted(p.name for p in s.cwd.iterdir())
        checks.append(("a demo leaves nothing behind: nothing written to the "
                       "directory it was started in",
                       left == [], f"{left}"))

    # The other configuration, which the note points at: resolution answers.
    with Served("--demo", "--authority", "demo.spatialdds.org",
                "--port", str(port + 1)) as s:
        base = f"http://127.0.0.1:{port + 1}"
        uri = "spatialdds://demo.spatialdds.org/south-mall/content/fountain-label"
        st, body = get(f"{base}/spatialdds/resolve?uri="
                       + urllib.parse.quote(uri, safe=""))
        doc = json.loads(body) if st == 200 else {}
        checks.append(("`--demo --authority demo.spatialdds.org` resolves the "
                       "packaged manifests, as the note promises",
                       st == 200 and doc.get("id") == uri, f"{st}"))
        checks.append(("…and advertises that authority while bound to "
                       "loopback, which is the shape a deployment has",
                       "https://demo.spatialdds.org" in s.banner
                       and "(bound 127.0.0.1" in s.banner,
                       f"{[l for l in s.banner.splitlines() if 'serving' in l]}"))

    return report("the packaged demo, served as a stranger would", checks,
                  "a pip install is enough, and the examples do not know the "
                  "difference")


if __name__ == "__main__":
    sys.exit(main())
