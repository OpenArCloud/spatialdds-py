#!/usr/bin/env python3
"""The OpenAPI document is generated, current, valid, complete, and served.

Appendix I rotted for three versions because prose described what nothing
regenerated. This document is born with its gate:

* **drift**: `tools/gen_openapi.py --check` regenerates from the type tables,
  the published schemas and the write rule, and fails if the committed
  artifact differs -- so a changed schema, table row or refusal that does not
  change the document reds here;
* **validity**: the document is OpenAPI 3.1 by `openapi-spec-validator`;
* **completeness, both ways**: every route the gateway registers is documented,
  and every documented operation is a route the gateway registers -- read from
  the live app's router, not from a list kept beside it;
* **served**: `/.well-known/spatialdds/openapi` returns the committed document
  with this endpoint's base as its server, and the reference page beside it.

    gates/openapi.py
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402
import yaml                                                    # noqa: E402

from harness import FOUNTAIN, MANIFESTS, Gateway, report, state  # noqa: E402

from spatialdds.server import gw_http, tenancy                 # noqa: E402

VERBS = ("get", "put", "post", "delete")


def template_regex(path: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+",
                                   re.escape(path)) + "$")


def instantiate(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    r = subprocess.run([sys.executable, "tools/gen_openapi.py", "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    checks.append(("the committed document is what the generator writes now "
                   "(no drift)", r.returncode == 0,
                   (r.stdout + r.stderr).strip()[-300:]))
    text = gw_http.OPENAPI.read_text()
    doc = yaml.safe_load(text)
    try:
        from openapi_spec_validator import validate
        validate(doc)
        valid, why = True, ""
    except Exception as e:                                     # noqa: BLE001
        valid, why = False, f"{type(e).__name__}: {str(e)[:200]}"
    checks.append(("the document is valid OpenAPI 3.1", valid, why))

    # Advertised as a base the committed document does not name, so the
    # server substitution is observable rather than a no-op.
    other = "https://registry.mytown.example"
    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=other,
                       publish=True).start()
    try:
        routes = sorted({(rr.method.lower(), rr.resource.canonical)
                         for rr in gw.app.router.routes()
                         if rr.method.lower() in VERBS
                         and rr.resource.canonical != "/"})
        documented = sorted((v, p) for p, item in doc["paths"].items()
                            for v in VERBS if v in item)
        missing = [f"{m.upper()} {p}" for m, p in routes
                   if not any(v == m and template_regex(p).match(instantiate(dp))
                              or v == m and template_regex(dp).match(
                                  instantiate(p))
                              for v, dp in documented)]
        invented = [f"{v.upper()} {dp}" for v, dp in documented
                    if not any(m == v and template_regex(p).match(
                        instantiate(dp)) for m, p in routes)]
        checks.append((f"every route the gateway registers is documented "
                       f"({len(routes)} routes)", not missing, f"{missing}"))
        checks.append((f"every documented operation is a route the gateway "
                       f"registers ({len(documented)} operations)",
                       not invented, f"{invented}"))

        statuses = {str(s) for s, _t, _w in tenancy.WRITE_REFUSALS.values()}
        writes = [(v, p) for v, p in documented if v in ("put", "delete")
                  or (v == "post" and "search" not in p)]
        short = [f"{v.upper()} {p}" for v, p in writes
                 if not statuses <= set(doc["paths"][p][v]["responses"])]
        checks.append((f"every write operation documents the write rule's "
                       f"refusals ({', '.join(sorted(statuses))})",
                       not short, f"{short}"))

        async with aiohttp.ClientSession() as s:
            async with s.get(gw.base + "/.well-known/spatialdds/openapi") as resp:
                body = await resp.text()
                ctype = resp.headers.get("Content-Type", "")
            want = text.replace(gw_http.OPENAPI_SERVER, f"- url: {other}\n", 1)
            checks.append(("the well-known path serves the committed "
                           "document, with this endpoint's base as server",
                           resp.status == 200 and body == want
                           and body != text and f"- url: {other}" in body
                           and ctype.startswith("application/yaml"),
                           f"{resp.status} {ctype}"))
            async with s.get(gw.base + "/.well-known/spatialdds/openapi.html"
                             ) as resp:
                page = await resp.text()
            ops = sum(1 for _v, _p in documented)
            checks.append(("the reference page is served, script-free, and "
                           "lists every operation",
                           resp.status == 200 and "<script" not in page
                           and page.count("<section id=") == ops,
                           f"{resp.status} {page.count('<section id=')} of {ops}"))
    finally:
        await gw.stop()
    return report("openapi", checks,
                  "the OpenAPI document is generated, current, valid, "
                  "complete, and served")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
