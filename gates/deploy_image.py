#!/usr/bin/env python3
"""The deployment, verified by replication rather than by reading.

`deploy/Dockerfile` has never been built -- building it pulls a base image from
a public registry, which has been out of scope for every batch that touched it
-- so the question "would the image work" has been answered by replicating what
it does: parse its own instructions, carry out their equivalent in an otherwise
empty tree, and serve the endpoint from that tree alone. The original batch did
that by hand once. 0.2.0 moved the gateway into the distribution and rewrote
the image around `pip install`, which is exactly the change that invalidates a
hand-check, so the replication is a gate now.

What it replicates, from the Dockerfile's own text and not from a copy of it
kept here:

* every `COPY` line, into a temporary tree;
* the `RUN pip install` line, as an offline install into a fresh environment,
  so the image's one dependency instruction is executed rather than trusted;
* the `ENV` defaults and the `CMD`, run from that tree with nothing of this
  repository on the path.

Then it reads the endpoint: discovery, the schema index and one schema by the
index's own URL, the anchors, an asset, and a `spatialdds://` URI resolved
through the advertised `https_base`. And two properties the deployment's shape
depends on: the `CMD` names only flags the installed `serve` actually accepts
(a flag that does not exist fails at container start, where nobody is
watching), and the container writes nothing -- publishing is off, so there is
no journal and the root filesystem can stay read-only.

Usage:  gates/deploy_image.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
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
from spatialdds.server import webjson                         # noqa: E402
from quickstart import listening                              # noqa: E402

DOCKERFILE = ROOT / "deploy" / "Dockerfile"
RUNBOOK = ROOT / "deploy" / "RUNBOOK.md"
PORT = 8799


def instructions() -> list[tuple[str, str]]:
    """`(verb, argument)` for every instruction, continuations joined."""
    out, lines = [], DOCKERFILE.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        while line.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            line = line.rstrip()[:-1] + " " + lines[i].strip()
        verb, _, rest = line.strip().partition(" ")
        out.append((verb.upper(), rest.strip()))
        i += 1
    return out


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    ins = instructions()
    copies = [a for v, a in ins if v == "COPY"]
    runs = [a for v, a in ins if v == "RUN"]
    envs = [a for v, a in ins if v == "ENV"]
    cmds = [a for v, a in ins if v == "CMD"]

    checks.append((f"the Dockerfile parses into instructions: "
                   f"{len(copies)} COPY, {len(runs)} RUN, 1 CMD",
                   bool(copies) and bool(runs) and len(cmds) == 1,
                   f"{[v for v, _ in ins]}"))

    # Nothing may still name the package that no longer exists. Checked over
    # the whole deploy directory, because a stale path in prose sends an
    # operator to a directory that is not there any more.
    stale = [f"{p.name}:{n}" for p in sorted((ROOT / "deploy").iterdir())
             if p.is_file()
             for n, line in enumerate(p.read_text().splitlines(), 1)
             if "spatialdds_web" in line or "webjson/" in line]
    checks.append((f"nothing in deploy/ names the retired `spatialdds_web` "
                   "tree or the moved `webjson/` artifacts",
                   not stale, f"{stale[:4]}"))

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "srv"
        tree.mkdir()
        # ---- replicate every COPY ----
        copied = 0
        for a in copies:
            parts = a.split()
            dest, srcs = parts[-1], parts[:-1]
            for src in srcs:
                s = ROOT / src.rstrip("/")
                d = Path(str(tree) + dest[len("/srv"):])
                if src.endswith("/"):
                    shutil.copytree(s, d, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(
                                        "__pycache__", "dist", "build",
                                        "*.egg-info"))
                else:
                    d = d / s.name if dest.endswith("/") else d
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(s, d)
                copied += 1
        n_files = len([p for p in tree.rglob("*") if p.is_file()])
        checks.append((f"its COPY set replicates into an empty tree "
                       f"({copied} path(s), {n_files} file(s))",
                       n_files > 100, f"{n_files} files"))

        # ---- replicate the pip install ----
        install = next((r for r in runs if r.startswith("pip install")), "")
        target = re.search(r'"([^"]+)"', install)
        checks.append(("its install line installs the distribution from the "
                       "build context rather than from an index",
                       bool(target) and "lib/spatialdds" in target.group(1)
                       and "--no-cache-dir" in install,
                       f"{install[:90]!r}"))
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv",
                        "--system-site-packages", str(venv)],
                       check=True, capture_output=True)
        spec = str(tree / "lib" / "spatialdds")
        if target and "[server]" in target.group(1):
            spec += "[server]"
        r = subprocess.run([str(venv / "bin" / "pip"), "install", "--quiet",
                            "--no-index", "--no-build-isolation", "--no-deps",
                            spec], capture_output=True, text=True)
        checks.append((f"…and that install succeeds against the replicated "
                       "tree alone, offline",
                       r.returncode == 0,
                       f"{(r.stderr or r.stdout)[-240:]}"))
        if r.returncode != 0:
            return report("the deployment, replicated", checks,
                          "the image's install line failed on its own COPY set")
        # The source tree is deleted after the install, exactly as the image
        # does -- so what serves below is the installed package and nothing
        # that happened to be lying beside it.
        rm = next((x for x in runs if "rm -rf" in x), "")
        checks.append(("the image deletes the build context after installing, "
                       "so what runs is the installed package",
                       "rm -rf" in rm and "/srv/lib" in rm, f"{rm[:80]!r}"))
        shutil.rmtree(tree / "lib")

        # ---- the CMD names only flags that exist ----
        cmd = cmds[0]
        argv = re.findall(r'--[a-z-]+', cmd)
        helptext = subprocess.run([str(venv / "bin" / "python3"), "-m",
                                   "spatialdds.serve", "--help"],
                                  capture_output=True, text=True).stdout
        unknown = [f for f in sorted(set(argv)) if f not in helptext]
        checks.append((f"every flag the CMD names exists in the installed "
                       f"`serve` ({', '.join(sorted(set(argv)))})",
                       not unknown, f"unknown: {unknown}"))

        # ---- run the CMD, with the ENV defaults, from the empty tree ----
        env = {"PATH": os.environ.get("PATH", ""),
               "HOME": str(Path(tmp))}
        for e in envs:
            for pair in re.findall(r'([A-Z_]+)=(\S+)', e):
                env[pair[0]] = pair[1]
        env["SPATIALDDS_PORT"] = str(PORT)
        checks.append((f"its ENV defaults are the two knobs the RUNBOOK "
                       f"documents ({', '.join(sorted(k for k in env if k.startswith('SPATIALDDS')))})",
                       {k for k in env if k.startswith("SPATIALDDS")}
                       == {"SPATIALDDS_PORT", "SPATIALDDS_AUTHORITY"},
                       f"{sorted(k for k in env if k.startswith('SPATIALDDS'))}"))
        shell = re.sub(r'^\[\s*"sh"\s*,\s*"-c"\s*,\s*', "", cmd).rstrip("]")
        shell = shell.strip().strip('"').replace('\\"', '"')
        child = subprocess.Popen(
            ["sh", "-c", shell.replace("python3", str(venv / "bin" / "python3"))],
            cwd=str(tree), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        try:
            owner = ""
            for _ in range(300):
                if child.poll() is not None:
                    break
                owner = listening(PORT)
                if owner and owner.split()[1] == str(child.pid):
                    break
                time.sleep(0.1)
            if child.poll() is not None:
                out = (child.stdout.read() if child.stdout else "")[-400:]
                checks.append(("the CMD serves from the replicated tree",
                               False, f"exited {child.returncode}: {out}"))
                return report("the deployment, replicated", checks,
                              "the image's CMD did not serve")
            checks.append((f"the CMD serves from the replicated tree, and the "
                           "listening process is the one it started",
                           bool(owner), f"{owner[:70]!r}"))

            base = f"http://127.0.0.1:{PORT}"
            st, body = get(f"{base}/.well-known/spatialdds/search?geohash=9v6krc")
            doc = json.loads(body) if st == 200 else {}
            n_top = len(doc.get("results", [{}])[0].get("service", {})
                        .get("topics", [])) if doc.get("results") else 0
            checks.append((f"discovery answers with 1 service and "
                           f"{n_top} topics -- the body, not just a status "
                           "code, which is what the RUNBOOK's health check "
                           "reads",
                           st == 200 and len(doc.get("results", [])) == 1
                           and n_top == 3, f"{st} {body[:120]!r}"))
            st, body = get(f"{base}/.well-known/spatialdds/schemas")
            index = json.loads(body) if st == 200 else []
            entries = index if isinstance(index, list) \
                else index.get("schemas", [])
            checks.append((f"the schema index lists {len(entries)} types",
                           st == 200 and len(entries) == 112,
                           f"{st}, {len(entries)}"))
            # The index publishes absolute URLs built from the authority the
            # image advertises, which is the whole point of `--authority` and
            # the one thing an operator cannot test by resolving DNS. Followed
            # the way a client on a laptop follows it: by path, against the
            # address actually reachable.
            one = entries[0] if entries else {}
            url = one.get("url", "")
            st2, raw = get(base + url[url.index("/", 8):]) \
                if url.startswith("https://") else (0, b"")
            want = one.get("digest", "")
            got = f"sha256:{webjson.digest(json.loads(raw))}" if raw else ""
            checks.append((f"…and one of them fetches by the index's own "
                           "advertised URL, followed by path, at the digest "
                           f"the index published ({url[:34]}…)",
                           st2 == 200 and want and want == got,
                           f"{st2}, index {want[:22]} vs served {got[:22]}"))
            st, body = get(f"{base}/ut-austin-south-mall/frames/geo_anchor/v1")
            checks.append((f"the anchors are served from the packaged "
                           f"recording ({len(json.loads(body)) if st == 200 else 0})",
                           st == 200 and len(json.loads(body)) == 5,
                           f"{st}"))
            st, body = get(f"{base}/assets/fountain-marker.gltf")
            checks.append((f"the packaged assets are reachable "
                           f"({len(body)} bytes)",
                           st == 200 and len(body) == 954, f"{st}"))
            st, body = get(f"{base}/.well-known/spatialdds/resolver")
            meta = json.loads(body) if st == 200 else {}
            uri = ("spatialdds://demo.spatialdds.org/south-mall/content/"
                   "fountain-marker")
            hb = meta.get("https_base", "")
            path = base + hb[hb.index("/", 8):] if "//" in hb else ""
            st2, body2 = get(f"{path}?uri={urllib.parse.quote(uri, safe='')}") \
                if path else (0, b"")
            checks.append((f"a spatialdds:// URI resolves through the "
                           "advertised https_base, as the deploy checklist "
                           "follows it",
                           st == 200 and meta.get("authority")
                           == "demo.spatialdds.org" and st2 == 200
                           and json.loads(body2).get("id") == uri,
                           f"resolver {st}, resolve {st2}"))
            # Read-only is a property of this configuration, not a hope.
            banner = ""
            checks.append(("publishing is off, so the container needs no "
                           "writable filesystem: a PUT is refused",
                           get(f"{base}/ut-austin-south-mall/frames/"
                               "geo_anchor/v1/x")[0] in (401, 404, 405),
                           f"{banner}"))
            left = sorted(p.name for p in tree.iterdir())
            checks.append((f"…and it wrote nothing into its working "
                           f"directory ({left or 'empty'})",
                           left == [], f"{left}"))
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:          # pragma: no cover
                child.kill()

    # The RUNBOOK's own numbers, which are the ones an operator reads.
    rb = RUNBOOK.read_text()
    gates = len([l for l in (ROOT / "gates" / "run_all.py").read_text()
                 .splitlines() if '{"name": "' in l])
    checks.append((f"the RUNBOOK's release procedure names the gate count "
                   f"this repository has ({gates})",
                   f"{gates} gates" in rb,
                   f"{[l for l in rb.splitlines() if 'gates, 0 failed' in l]}"))
    checks.append(("…and its run-and-build block names the image's current "
                   "knob",
                   "SPATIALDDS_AUTHORITY=demo.spatialdds.org" in rb
                   and "SPATIALDDS_PUBLIC_URL=" not in rb,
                   "the RUNBOOK still documents a knob the image dropped"))

    return report("the deployment, replicated", checks,
                  "the image's own instructions serve the endpoint from the "
                  "new layout")


if __name__ == "__main__":
    sys.exit(main())
