#!/usr/bin/env python3
"""The MCP gates' plants: each one a defect the gates must catch, by name.

A gate that cannot fail when its subject is broken is not a gate. Each plant
below is a small edit that breaks one thing the server, its packaging, its
guide or its fixtures promise; for each, this copies the tree (tracked and
new files, nothing ignored) into a directory of its own, makes the edit,
confirms it landed, runs the gate that should catch it, and requires that
gate to fail with the plant's own check among its failures.

    gates/mcp_plants.py            every plant
    gates/mcp_plants.py NAME ...   the ones named

Not part of run_all: it runs the MCP gates once per plant, and they run in
run_all already. Run it after changing a gate or the server.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = "lib/spatialdds/spatialdds/mcp_server.py"
GUIDE = "docs/mcp-server.md"

#: (name, file, old, new, gate argv, the check that must fail)
PLANTS = [
    ("base-gains-mcp", "lib/spatialdds/pyproject.toml",
     'dependencies = ["requests>=2.25", "websockets>=12"]',
     'dependencies = ["requests>=2.25", "websockets>=12", "mcp>=2.3,<3"]',
     ["gates/mcp_server.py", "--only", "A"],
     "the base install's dependencies are"),
    ("no-script", "lib/spatialdds/pyproject.toml",
     'spatialdds-mcp = "spatialdds.mcp_server:main"\n', "",
     ["gates/mcp_server.py", "--only", "A"],
     "`spatialdds-mcp` is installed"),
    ("imports-the-gateway", SERVER,
     "from .events import Dispose, EndOfReplay, Liveliness, Sample\n",
     "from .events import Dispose, EndOfReplay, Liveliness, Sample\n"
     "from .server import gateway  # noqa: F401  planted\n",
     ["gates/mcp_server.py", "--only", "B"],
     "import statements name no DDS"),
    ("carries-a-token", SERVER,
     "connect(base, lifted=False, timeout=timeout)",
     'connect(base, token="planted-token", lifted=False, timeout=timeout)',
     ["gates/mcp_server.py", "--only", "B"],
     "no request the server made carried a credential"),
    ("lost-is-ignored", SERVER,
     'h.liveliness = "reported" if ev.alive else "lost"',
     'h.liveliness = "reported"',
     ["gates/mcp_server.py", "--only", "C"],
     "a lost writer's claim is kept and says so"),
    ("lost-is-dropped", SERVER,
     "rows = [c for c in rows if keep(c)]",
     'rows = [c for c in rows if keep(c) and c["liveliness"] != "lost"]',
     ["gates/mcp_server.py", "--only", "C"],
     "nothing was dropped"),
    ("replay-is-evidence", SERVER,
     "                if self.live:\n                    # A write after",
     "                if True:\n                    # A write after",
     ["gates/mcp_server.py", "--only", "C"],
     "a replayed claim is not evidence"),
    ("withdrawn-is-forgotten", SERVER,
     "            return [(k, Heard(**vars(h))) for k, h in self.heard.items()",
     "            return [] and [(k, Heard(**vars(h))) for k, h in "
     "self.heard.items()",
     ["gates/mcp_server.py", "--only", "C"],
     "a withdrawn claim is still returned"),
    ("ranked-by-asserter", SERVER,
     'rows.sort(key=lambda c: order[tuple(c["proposition"])])',
     'rows.sort(key=lambda c: (order[tuple(c["proposition"])], '
     'c["claim"].get("asserter", "")))',
     ["gates/mcp_server.py", "--only", "C"],
     "claims about one proposition come back side by side"),
    ("basis-rewritten", SERVER,
     '            "claim": rec,\n',
     '            "claim": {**rec, "basis": str(rec.get("basis", "")).lower()},'
     "\n",
     ["gates/mcp_server.py", "--only", "C"],
     "every claim is the binding's record, verbatim"),
    ("near-ignores-the-box", SERVER,
     '    if r.get("extent"):\n        lo, hi = r["extent"]',
     '    if False and r.get("extent"):\n        lo, hi = r["extent"]',
     ["gates/mcp_server.py", "--only", "C"],
     "near measures to an entity's box"),
    ("age-off-by-a-minute", SERVER,
     "age = None if t is None else round(self.clock() - t, 1)",
     "age = None if t is None else round(self.clock() - t + 60, 1)",
     ["gates/mcp_server.py", "--only", "C"],
     "age is now minus the stamp"),
    ("unreadable-is-dropped", SERVER,
     '                failed.append({"source": src.url, "error": str(e)})\n'
     "                continue",
     "                continue",
     ["gates/mcp_server.py", "--only", "C"],
     "an endpoint that cannot be read is listed"),
    ("no-throttle", SERVER,
     "            if tokens >= 1:\n                self.buckets[who]",
     "            if True:\n                self.buckets[who]",
     ["gates/mcp_server.py", "--only", "D"],
     "--throttle refuses a burst"),
    ("front-is-one-client", SERVER,
     'proxy_headers=True, forwarded_allow_ips="127.0.0.1,::1")',
     'proxy_headers=False)',
     ["gates/mcp_server.py", "--only", "D"],
     "counting each forwarded client on its own"),
    ("transcript-byte", "samples/mcp/transcript-stdio-venue-live.jsonl",
     '"transcript": "stdio-venue-live"', '"transcript": "stdio-venue-livE"',
     ["gates/mcp_server.py", "--only", "E"],
     "every fixture in samples/mcp matches SHA256SUMS"),
    ("guide-verb", GUIDE,
     "svc:model:demo/venue declares ent:shallows:littlefield P361 "
     "ent:pond:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)\n"
     "svc:mover:demo/ducks declares ent:duck:catalog-pose",
     "svc:model:demo/venue states ent:shallows:littlefield P361 "
     "ent:pond:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)\n"
     "svc:mover:demo/ducks declares ent:duck:catalog-pose",
     ["gates/mcp_guide.py"],
     '`get_claims` {"scene": "ut-austin-littlefield"} prints'),
    ("guide-install-without-extra", GUIDE,
     'pip install "spatialdds[mcp]==0.8.2"', 'pip install "spatialdds==0.8.2"',
     ["gates/mcp_guide.py"],
     "the install line names the extra"),
    ("days-rounded", SERVER,
     '    return f"{age / 86400:.1f} d"', '    return f"{age / 86400:.0f} d"',
     ["gates/mcp_guide.py"],
     '`get_entities` {"scene": "ut-austin-littlefield", "near"'),
    ("guide-config-other-command", GUIDE,
     '"type": "stdio",\n      "command": "spatialdds-mcp",',
     '"type": "stdio",\n      "command": "spatialdds-serve",',
     ["gates/mcp_guide.py"],
     "the claude-code configuration runs `spatialdds-mcp`"),
]


def tree_copy(dest: Path) -> None:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co",
                            "--exclude-standard", "-z"], capture_output=True,
                           check=True).stdout.decode().split("\0")
    for f in filter(None, files):
        src = ROOT / f
        if src.is_file():
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f)


def run(plant: tuple) -> tuple[bool, str]:
    name, rel, old, new, argv, check = plant
    with tempfile.TemporaryDirectory(prefix=f"plant-{name}-") as tmp:
        tmp = Path(tmp)
        tree_copy(tmp)
        f = tmp / rel
        text = f.read_text()
        if text.count(old) != 1:
            return False, (f"MISFIRE: the plant's text occurs "
                           f"{text.count(old)} times in {rel}")
        f.write_text(text.replace(old, new))
        if f.read_text() == text or (new and new not in f.read_text()):
            return False, f"MISFIRE: {rel} unchanged after the edit"
        r = subprocess.run([sys.executable, *argv], cwd=tmp,
                           capture_output=True, text=True, timeout=900,
                           env={**_env(), "SPATIALDDS_GATE_GIT": str(ROOT)})
        fails = [l.strip()[5:] for l in r.stdout.splitlines()
                 if l.startswith("  FAIL ")]
        hit = [x for x in fails if check in x]
        if r.returncode == 0:
            return False, f"the gate PASSED with the plant in ({argv[0]})"
        if not hit:
            return False, (f"the gate failed, but not on its check "
                           f"{check!r}: {fails[:3] or r.stdout[-300:]}"
                           f"{r.stderr[-300:]}")
        return True, (f"{argv[0]} failed {len(fails)} check(s), its own "
                      f"among them: {hit[0][:90]}")


def _env() -> dict:
    import os
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def main() -> int:
    names = sys.argv[1:]
    chosen = [p for p in PLANTS if not names or p[0] in names]
    results = []
    for p in chosen:
        ok, why = run(p)
        results.append((p[0], ok, why))
        print(f"  {'ok  ' if ok else 'FAIL'} {p[0]}: {why}", flush=True)
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results)} plant(s): {len(results) - len(bad)} caught by "
          f"their own check, {len(bad)} not")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
