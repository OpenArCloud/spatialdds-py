#!/usr/bin/env python3
"""The published duck, followed from PUT to fetched bytes.

Three claims, each checked against something other than the gateway:

**Write to bytes.** The duck's manifest is published, its URI resolved, its
ref fetched, and the bytes byte-compared against the committed
`samples/assets/duck.gltf`. Expected bytes come from the committed file; the
hash the manifest declares is recomputed from it too, so an edit to either
side without the other fails here rather than later.

**Siblings, not replacements.** After the duck is published the content
collection holds the recording's two content records *and* the duck. This is
the content-side analogue of the one-manifest-per-scene defect search had: a
published record that displaced a recorded one would still look perfect in
isolation.

**The VPS loop.** The service manifest derived from the specification's own
`manifests/v1.8/vps_manifest.json` is published and resolved, with its own
`ttl_sec` governing `max-age`. The spec's example, live as a record.

Usage:  gates/publish_duck.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from publish_common import (ROOT, aiohttp, authored, authority_base, get,  # noqa: E402
                            put, resolve_path, seeded)
from harness import Gateway, report                            # noqa: E402
from spatialdds_web import gateway                             # noqa: E402

DUCK = ROOT / "samples" / "duck.input.json"


async def main() -> int:
    duck = json.loads(DUCK.read_text())
    doc = authored()
    base = authority_base(duck)
    checks: list[tuple[str, bool, str]] = []
    gw = await Gateway(seeded(duck), advertise=base, publish=True).start()

    committed = (ROOT / duck["asset"]["path"]).read_bytes()
    checks.append((f"the committed asset is {duck['asset']['bytes']} bytes, "
                   f"as the input declares", len(committed)
                   == duck["asset"]["bytes"], f"{len(committed)}"))
    checks.append(("and its digest is the one the manifest declares",
                   "sha256:" + hashlib.sha256(committed).hexdigest()
                   == duck["manifest"]["assets"][0]["hash"],
                   "the input and the committed file disagree"))

    async with aiohttp.ClientSession() as s:
        man, ann = duck["manifest"], duck["announce"]
        stm, _h, _b = await put(s, gw.base, gateway.RESOLVE_PATH, man)
        aurl = f"{ann['path']}/{ann['key'].replace('/', '%2F')}"
        sta, _h2, ba = await put(s, gw.base, aurl, ann["record"])
        checks.append((f"the duck's manifest and announce are accepted "
                       f"(201/201)", (stm, sta) == (201, 201),
                       f"got {stm}/{sta}: {ba[:140]!r}"))
        checks.append(("the served announce is the authored record, field for "
                       "field", json.loads(ba) == ann["record"], "differs"))

        # Siblings. The recording's own content must still be there.
        _st, _h, bc = await get(s, gw.base, ann["path"])
        ids = sorted(c["content_id"] for c in json.loads(bc))
        want = sorted(["content/duck", "content/fountain-marker",
                       "content/fountain-label"])
        checks.append((f"the content collection holds the duck and both "
                       f"recorded records ({ids})", ids == want, f"{ids}"))
        _st, _h, bs = await get(
            s, gw.base,
            f"/.well-known/spatialdds/search?geohash={duck['geohash']}")
        svc = json.loads(bs)["results"]
        checks.append((f"and a geohash search over the scene still answers "
                       f"({len(svc)} service manifest(s))", len(svc) >= 1,
                       "the scene disappeared from search"))

        # Write to bytes.
        stn, hn, bn = await get(s, gw.base, resolve_path(man["id"]))
        back = json.loads(bn) if bn else {}
        checks.append(("the duck's URI resolves (200)", stn == 200,
                       f"got {stn}"))
        checks.append(("the resolved manifest is the authored one",
                       back == man, "differs"))
        checks.append((f"its max-age is the manifest's own ttl_sec "
                       f"({man['ttl_sec']})",
                       f"max-age={man['ttl_sec']}" in hn.get("Cache-Control", ""),
                       f"{hn.get('Cache-Control')!r}"))
        ref = back.get("assets", [{}])[0]
        path = "/" + ref.get("uri", "").split("/", 3)[-1]
        stb, hb, raw = await get(s, gw.base, path)
        checks.append((f"its ref fetches (200 from {path})", stb == 200,
                       f"got {stb}: {raw[:120]!r}"))
        checks.append((f"the fetched bytes are the committed asset, byte for "
                       f"byte ({len(raw)} bytes)", raw == committed,
                       f"{len(raw)} fetched vs {len(committed)} committed"))
        checks.append((f"served as {ref.get('media_type')}",
                       hb.get("Content-Type", "").startswith(
                           ref.get("media_type", "x")),
                       f"{hb.get('Content-Type')!r}"))
        checks.append(("the asset's ETag is the digest of its bytes",
                       hb.get("ETag") == f'"sha256:'
                       f'{hashlib.sha256(committed).hexdigest()}"',
                       f"{hb.get('ETag')!r}"))
        # Over HTTP first, and it does reach the handler: aiohttp matches the
        # raw segment and URL-decodes afterwards, so `..%2F..%2FREADME.md`
        # arrives as a name containing slashes. The probe names a file that
        # exists -- an earlier version named one that does not, and so
        # returned 404 for the wrong reason and passed with the containment
        # rule deleted.
        checks.append(("an encoded traversal in an asset name is a 404",
                       (await get(s, gw.base, "/assets/..%2F..%2FREADME.md")
                        )[0] == 404, "it was served"))
        # And at the function, over names HTTP would not deliver.
        from spatialdds_web.gw_http import asset_path
        aroot = ROOT / "samples" / "assets"
        escapes = {n: asset_path(aroot, n) for n in
                   ("../README.md", "../../README.md", "..",
                    "../manifests/content-fountain-marker.json")}
        checks.append((f"and the containment rule refuses every name that "
                       f"leaves the directory ({len(escapes)} tried)",
                       all(v is None for v in escapes.values()),
                       f"{ {k: str(v) for k, v in escapes.items() if v} }"))
        checks.append(("while admitting the asset itself, and a name that "
                       "wanders out and back",
                       asset_path(aroot, "duck.gltf") is not None
                       and asset_path(aroot, "../assets/duck.gltf")
                       is not None, "a legitimate name was refused"))

        # The VPS loop: the specification's own example, live as a record.
        vman = doc["service_manifest"]
        stv, _h, bv = await put(s, gw.base, gateway.RESOLVE_PATH, vman)
        checks.append((f"the derived VPS manifest is accepted (201)",
                       stv == 201, f"got {stv}: {bv[:140]!r}"))
        stw, hw, bw = await get(s, gw.base, resolve_path(vman["id"]))
        checks.append(("it resolves through the resolver (200)", stw == 200,
                       f"got {stw}"))
        checks.append(("and is the manifest that was authored",
                       json.loads(bw) == vman, "differs"))
        checks.append((f"with its own ttl_sec ({vman['ttl_sec']}) as max-age, "
                       f"not the endpoint default "
                       f"({gateway.PUBLIC_MAX_AGE})",
                       f"max-age={vman['ttl_sec']}" in hw.get("Cache-Control", ""),
                       f"{hw.get('Cache-Control')!r}"))
        svc_ann = doc["service"]
        stsa, _h, _b = await put(
            s, gw.base, f"{svc_ann['path']}/{svc_ann['key']}",
            svc_ann["record"])
        _st, _h, bq = await get(s, gw.base, "/.well-known/spatialdds/search")
        found = sorted(r["service"]["service_id"]
                       for r in json.loads(bq)["results"])
        checks.append((f"the announce the manifest belongs to is findable by "
                       f"search, closing the loop ({found})",
                       stsa == 201
                       and svc_ann["record"]["service_id"] in found,
                       f"PUT {stsa}, found {found}"))
        checks.append(("and the announce's manifest_uri is the URI that "
                       "resolves",
                       svc_ann["record"]["manifest_uri"] == vman["id"],
                       f"{svc_ann['record']['manifest_uri']} vs {vman['id']}"))

    await gw.stop()
    return report("publish duck", checks,
                  "a published record is followed from PUT to fetched bytes")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
