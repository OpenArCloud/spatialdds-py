#!/usr/bin/env python3
"""One gateway, two halves, and no import between them.

0.2.0 moved the reference gateway into the distribution, because
`pip install "spatialdds[server]"` has to stand up a registry with no clone.
That makes one package hold two things that must not become one thing:

* the **client** -- `requests` and `websockets` talking to an endpoint;
* the **server** -- `aiohttp` and `mcap` being one.

Three claims, and all three are cheap to break by accident. A convenience
import in `client.py` would make every client install carry `aiohttp`. An
eager `import requests` in the package's `__init__` would make every registry
carry the client's dependencies, because importing `spatialdds.server` runs
the parent's `__init__` first. And a second copy of the gateway anywhere in
the tree -- the obvious way to "just get the package working" -- would put the
repository back to two implementations that drift.

So this gate reads the import statements, and then checks the runtime, because
the two can disagree: `from spatialdds import server` asks the package for an
attribute before the import machinery tries the submodule, and an earlier
version of the facade answered that question by importing the client. Static
reading alone said the halves were separate; `sys.modules` said otherwise.

Usage:  gates/import_graph.py [--census]
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"
sys.path.insert(0, str(LIB))

from harness import report                                    # noqa: E402

PKG = LIB / "spatialdds"
SERVER = PKG / "server"
#: The client half: the API and what it needs. `serve.py` is deliberately not
#: here -- it is the server's command line, and it lives in the parent
#: namespace because `python -m spatialdds.serve` is the line people type.
CLIENT_FILES = ("client.py", "canonical.py", "errors.py", "records.py")
SERVER_ONLY = ("aiohttp", "mcap", "jsonschema")
#: The modules that are the gateway. Exactly one copy of each, anywhere.
GATEWAY_MODULES = ("idlmodel.py", "xcdr2.py", "webjson.py", "gateway.py",
                   "gw_http.py", "publish.py", "pin.py")


def imports(path: Path) -> list[str]:
    """Every module name this file imports, relative ones resolved to dots."""
    out = []
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(("." * n.level) + (n.module or ""))
    return out


def probe(code: str, block: tuple[str, ...] = ()) -> str:
    """Run code with only the package on the path, and nothing of this repo.

    `block` names top-level packages a meta-path hook refuses, which is how
    "the server does not need the client's dependencies" becomes a fact about
    an interpreter rather than a claim about a file.
    """
    pre = ""
    if block:
        pre = (f"import sys\n"
               f"_B = {block!r}\n"
               f"class _Block:\n"
               f"    def find_spec(self, name, path=None, target=None):\n"
               f"        if name.split('.')[0] in _B:\n"
               f"            raise ImportError(name + ' blocked by the gate')\n"
               f"        return None\n"
               f"sys.meta_path.insert(0, _Block())\n")
    r = subprocess.run([sys.executable, "-c", pre + code], cwd=str(ROOT),
                       capture_output=True, text=True,
                       env={"PATH": os.environ.get("PATH", ""),
                            "PYTHONPATH": str(LIB)})
    return (r.stdout + r.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", action="store_true",
                    help="print the whole local import graph and stop")
    a = ap.parse_args()

    files = sorted(p for p in PKG.rglob("*.py")
                   if "__pycache__" not in p.parts)
    if a.census:
        for p in files + sorted((ROOT / "gates").glob("*.py")) \
                + sorted((ROOT / "tools").glob("*.py")):
            local = [m for m in imports(p)
                     if m.startswith(".") or m.split(".")[0] == "spatialdds"]
            third = [m.split(".")[0] for m in imports(p)
                     if m.split(".")[0] in SERVER_ONLY + ("requests",
                                                          "websockets")]
            print(f"  {p.relative_to(ROOT).as_posix():<52} "
                  f"local={','.join(sorted(set(local))) or '-':<34} "
                  f"third={','.join(sorted(set(third))) or '-'}")
        return 0

    checks: list[tuple[str, bool, str]] = []

    # ---- static: the server half ----
    bad = []
    for p in sorted(SERVER.glob("*.py")):
        for m in imports(p):
            if m in (".client", ".canonical", ".errors", ".records") \
                    or m.startswith("spatialdds.client") \
                    or m.startswith("spatialdds.canonical") \
                    or m == "requests" or m == "websockets":
                bad.append(f"{p.name} imports {m}")
    checks.append((f"no module under spatialdds/server/ imports the client "
                   f"half or its two dependencies "
                   f"({len(list(SERVER.glob('*.py')))} modules read)",
                   not bad, "; ".join(bad[:3])))

    # The one thing the server may reach up for, named so that it is an
    # allowance rather than a gap: `spatialdds.canonjson` is Appendix N.1's
    # canonical form, which both halves must produce byte-identically. It
    # belongs to neither of them -- no dependencies, no knowledge of either
    # side -- and it is shared for the same reason `artifacts/` is shared as
    # data. Any *other* import from the parent package would be the coupling
    # this gate exists to prevent.
    reach = sorted({m for q in SERVER.glob("*.py") for m in imports(q)
                    if m.startswith("..")})
    checks.append((f"the only name the server reaches out of its own package "
                   f"for is the shared canonical-JSON leaf "
                   f"({', '.join(reach) or 'none'})",
                   reach in ([], ["..canonjson"]), f"{reach}"))
    shared = sorted(q.name for q in PKG.glob("*.py")
                    if not imports(q) or all(
                        m.split(".")[0] in sys.stdlib_module_names
                        for m in imports(q) if not m.startswith(".")))
    checks.append((f"…and that leaf imports nothing but the standard library, "
                   f"so sharing it costs neither half anything "
                   f"({', '.join(shared)})",
                   "canonjson.py" in shared, f"{shared}"))

    # ---- static: the client half ----
    bad = []
    for name in CLIENT_FILES:
        p = PKG / name
        if not p.is_file():
            continue
        for m in imports(p):
            if m.startswith(".server") or m.startswith("spatialdds.server") \
                    or m.split(".")[0] in SERVER_ONLY:
                bad.append(f"{name} imports {m}")
    present = [n for n in CLIENT_FILES if (PKG / n).is_file()]
    checks.append((f"no client module imports the server or its dependencies "
                   f"({', '.join(present)})", not bad, "; ".join(bad[:3])))

    # ---- static: the facade ----
    facade = [m for m in imports(PKG / "__init__.py")
              if not m.startswith(".") and m.split(".")[0] not in
              sys.stdlib_module_names]
    checks.append(("the package's __init__ imports nothing third-party, so "
                   "importing either half costs nothing of the other",
                   not facade, f"{facade}"))

    # ---- static: one gateway ----
    dupes = {}
    for name in GATEWAY_MODULES:
        found = sorted(p.relative_to(ROOT).as_posix()
                       for p in ROOT.rglob(name)
                       if not {"__pycache__", "build", "dist"} & set(p.parts))
        if found != [(SERVER / name).relative_to(ROOT).as_posix()]:
            dupes[name] = found
    checks.append((f"exactly one copy of each of the {len(GATEWAY_MODULES)} "
                   f"gateway modules exists, all under spatialdds/server/",
                   not dupes, f"{dupes}"))

    # ---- static: the shim is a shim ----
    shim = ROOT / "tools" / "run_gateway.py"
    text = shim.read_text()
    # Statements, not lines: the file is mostly prose explaining why the two
    # defaults differ between a checkout and a wheel, and prose is not logic.
    stmts = [n for n in ast.walk(ast.parse(text))
             if isinstance(n, ast.stmt)
             and not (isinstance(n, ast.Expr)
                      and isinstance(n.value, ast.Constant)
                      and isinstance(n.value.value, str))]
    body = text.split('"""', 2)[-1]
    checks.append((f"tools/run_gateway.py is a shim: {len(stmts)} statements, "
                   f"hands over to spatialdds.serve, and serves nothing "
                   f"itself",
                   len(stmts) < 30
                   and "from spatialdds import serve" in text
                   and "serve.main(" in text
                   and "aiohttp" not in body and "make_app" not in body,
                   f"{len(stmts)} statements; "
                   f"aiohttp mentioned: {'aiohttp' in body}"))

    # ---- runtime: the client, alone ----
    out = probe(
        "import sys, json\n"
        "import spatialdds as sd\n"
        "assert sd.SPEC_COMMIT\n"
        "print(json.dumps(sorted(m for m in sys.modules\n"
        "    if m.split('.')[0] in ('aiohttp','mcap','jsonschema')\n"
        "    or m in ('spatialdds.server','spatialdds.client'))))")
    checks.append(("at runtime `import spatialdds` loads no server module "
                   "and none of the server's dependencies",
                   out == "[]", f"{out[-200:]!r}"))

    # ---- runtime: the server, with the client's dependencies forbidden ----
    out = probe(
        "import sys\n"
        "from spatialdds.server import gateway, gw_http, publish, pin\n"
        "from spatialdds import serve\n"
        "m, _ = pin.wire_model()\n"
        "print('types', len(m.aggregates),\n"
        "      '| client loaded', 'spatialdds.client' in sys.modules)",
        block=("requests", "websockets"))
    checks.append(("…and the whole server, plus `spatialdds.serve`, imports "
                   "with requests and websockets forbidden outright",
                   out.endswith("| client loaded False")
                   and out.startswith("types "), f"{out[-300:]!r}"))

    # ---- runtime: the server extra's dependencies are the declared ones ----
    out = probe("from spatialdds.server import gw_http\nprint('ok')",
                block=("aiohttp",))
    checks.append(("the server really needs aiohttp rather than optionally "
                   "importing it: forbidding aiohttp breaks gw_http, which "
                   "is why the extra declares it",
                   "ok" not in out and "blocked by the gate" in out,
                   f"{out[-200:]!r}"))
    out = probe("from spatialdds import serve\nserve.main(['--help'])",
                block=("aiohttp",))
    checks.append(("and `python -m spatialdds.serve` without the extra says "
                   "so in a sentence instead of a traceback",
                   'pip install "spatialdds[server]"' in out,
                   f"{out[-300:]!r}"))

    # ---- the native layer: a third half ----
    # It may use the gateway's codec, which is the point (one JSON, ruling 5):
    # the four stdlib-only modules that parse, encode, decode and project.
    # Nothing that serves, holds state or needs aiohttp, and nothing of the
    # client.
    codec_ok = {"..server", "..server.idlmodel", "..server.pin",
                "..server.webjson", "..server.xcdr2", "..server.mcapmeta"}
    native = sorted(p for p in (PKG / "dds").glob("*.py"))
    bad = []
    for p in native:
        for m in imports(p):
            if m.startswith("..server") and m not in codec_ok:
                bad.append(f"{p.name} imports {m}")
            if m in ("..client", "requests", "websockets", "aiohttp") \
                    or m.startswith("spatialdds.client"):
                bad.append(f"{p.name} imports {m}")
    # `from ..server import idlmodel, pin` reads as "..server": the names
    # imported are the ones that matter.
    for p in native:
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.level == 2 \
                    and n.module == "server":
                for a in n.names:
                    if f"..server.{a.name}" not in codec_ok:
                        bad.append(f"{p.name} imports ..server.{a.name}")
    checks.append((f"the native layer uses only the gateway's codec "
                   f"(idlmodel, pin, webjson, xcdr2) and the recording's "
                   f"metadata names (mcapmeta), and nothing of the client "
                   f"({len(native)} modules read)", not bad, "; ".join(bad[:3])))
    leaf = [m for m in imports(PKG / "events.py")
            if not m.startswith(".") and m.split(".")[0]
            not in sys.stdlib_module_names]
    checks.append(("`spatialdds.events`, which both halves yield, imports "
                   "nothing but the standard library", not leaf, f"{leaf}"))

    out = probe(
        "import spatialdds as sd\n"
        "try:\n"
        "    sd.participant('plaza', domain=1)\n"
        "    print('NO REFUSAL')\n"
        "except sd.MissingExtra as e:\n"
        "    print('refused:', e, '| ImportError:', isinstance(e, ImportError))\n"
        "try:\n"
        "    from spatialdds.dds.types.spatial.owm import Entity\n"
        "    print('TYPES IMPORTED')\n"
        "except sd.MissingExtra:\n"
        "    print('types refused too')\n"
        "print('connect', callable(sd.connect))\n"
        "print('frames', round(sd.frames.geodetic_of(\n"
        "    sd.frames.enu_to_ecef_pose(30.0, -97.0, 10.0)[0])[2], 6))\n"
        "print('recency', sd.Recency().threshold('k'))\n"
        "from spatialdds.dds import registry\n"
        "print('topic', registry.topic_name('plaza', 'r', 'geopose'))",
        block=("cyclonedds",))
    checks.append(("with cyclonedds forbidden, `sd.participant` and the "
                   "generated types raise `sd.MissingExtra` (an ImportError) "
                   "naming the extra",
                   'refused: the native layer needs cyclonedds: pip install '
                   '"spatialdds[dds]" | ImportError: True' in out
                   and "types refused too" in out,
                   f"{out[-300:]!r}"))
    checks.append(("…while the web client, the frames, Recency and the "
                   "registry all still work",
                   "connect True" in out and "frames 10.0" in out
                   and "recency 1.0" in out
                   and "topic spatialdds/plaza/r/geopose/v1" in out,
                   f"{out[-300:]!r}"))
    out = probe(
        "import sys\n"
        "import spatialdds as sd\n"
        "f = sd.participant\n"
        "from spatialdds.dds import codec, registry, qos\n"
        "print(f.__name__, '| client', 'spatialdds.client' in sys.modules,\n"
        "      '| gw_http', 'spatialdds.server.gw_http' in sys.modules)",
        block=("requests", "websockets", "aiohttp"))
    checks.append(("with requests, websockets and aiohttp forbidden, the "
                   "native layer imports, and loads neither the client nor "
                   "the HTTP server",
                   out.endswith("participant | client False | gw_http False"),
                   f"{out[-300:]!r}"))

    return report("the import graph: one gateway, two halves", checks,
                  "neither half imports the other, and there is one of each")


if __name__ == "__main__":
    sys.exit(main())
