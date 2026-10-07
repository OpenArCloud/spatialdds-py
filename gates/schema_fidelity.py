#!/usr/bin/env python3
"""The endpoint publishes the generated schemas, and serves what they describe.

A published schema nothing was checked against is documentation, not a
contract. So: every index entry matches the vendored file's digest, the
vendored set matches what PROVENANCE recorded when it was copied, and every
latched instance the endpoint serves validates against its own schema fetched
over HTTP exactly as a consumer would fetch it.

Usage:  gates/schema_fidelity.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402
import jsonschema                                              # noqa: E402

from harness import Gateway, TOKEN, report, state              # noqa: E402
from spatialdds_web import webjson                             # noqa: E402

WK = "/.well-known/spatialdds"


def provenance() -> dict[str, str]:
    out = {}
    for line in (ROOT / "webjson" / "PROVENANCE").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def local_schemas() -> dict[str, dict]:
    out = {}
    for p in sorted((ROOT / "webjson" / "schemas").glob("*.json")):
        d = json.loads(p.read_text())
        out[d.get("title") or p.stem] = d
    return out


async def run() -> int:
    st = state()
    gw = await Gateway(st).start()
    prov, local = provenance(), local_schemas()
    checks: list[tuple[str, bool, str]] = []

    async with aiohttp.ClientSession() as s:
        async with s.get(gw.base + f"{WK}/schemas") as r:
            index = await r.json()
            code = r.status
        checks.append((f"the schemas index is served ({len(index)} entries)",
                       code == 200 and isinstance(index, list) and index,
                       f"status {code}"))
        checks.append(("every entry carries exactly type, url and digest",
                       all(sorted(e) == ["digest", "type", "url"]
                           for e in index), ""))
        served = {r_.fqn for r_ in st.resources.values()}
        missing = sorted(served - {e["type"] for e in index})
        checks.append((f"every one of the {len(served)} served types is "
                       f"indexed", not missing, f"missing {missing}"))

        bad = []
        for e in index:
            path = e["url"][len(gw.base):]
            async with s.get(gw.base + path) as r2:
                if r2.status != 200:
                    bad.append(f"{e['type']}: {r2.status}")
                    continue
                doc = await r2.json()
                etag = r2.headers.get("ETag", "")
            if doc != local.get(e["type"]):
                bad.append(f"{e['type']}: body differs from vendored")
            elif (etag != f'"sha256:{webjson.digest(doc)}"'
                  or e["digest"] != f"sha256:{webjson.digest(doc)}"):
                bad.append(f"{e['type']}: digest/ETag")
        checks.append((f"all {len(index)} schemas fetch, equal the vendored "
                       f"copy, and their ETag is their content digest",
                       not bad, "; ".join(bad[:3])))

        agg = hashlib.sha256()
        for p in sorted((ROOT / "webjson" / "schemas").glob("*.json")):
            agg.update(p.name.encode())
            agg.update(hashlib.sha256(p.read_bytes()).digest())
        checks.append((f"the vendored set matches its recorded digest "
                       f"({prov.get('schemas.count')} files)",
                       agg.hexdigest() == prov.get("schemas.set_sha256"), ""))
        tb = ROOT / "webjson" / "serializer_tables.json"
        checks.append(("the vendored serializer tables match their digest",
                       hashlib.sha256(tb.read_bytes()).hexdigest()
                       == prov.get("serializer_tables.sha256"), ""))

        n, inval = 0, []
        hdr = {"Authorization": f"Bearer {TOKEN}"}
        for path in sorted(st.resources):
            res = st.resources[path]
            if not res.latched:
                continue
            async with s.get(gw.base + path, headers=hdr) as r3:
                if r3.status != 200:
                    continue
                docs = await r3.json()
            for d in docs:
                n += 1
                try:
                    jsonschema.validate(d, local[res.fqn])
                except jsonschema.ValidationError as e:
                    inval.append(f"{res.fqn}: {e.message[:60]}")
        checks.append((f"all {n} served latched instance(s) validate",
                       not inval and n > 0, "; ".join(inval[:2]) or
                       "nothing validated"))

    await gw.stop()
    return report("schema fidelity", checks,
                  "the endpoint publishes and obeys the generated schemas")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
