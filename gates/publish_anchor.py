#!/usr/bin/env python3
"""A published anchor, and the content it names, checked end to end.

Four claims, all of them against the authored input or the committed asset and
none against the gateway:

**Golden.** Every served field of the published anchor equals the authored
record, its ETag is the digest of its canonical form, and it validates against
the vendored schema -- the identical checks the recording's own five anchors
pass. A published anchor is not a second class of anchor.

**Siblings.** The latched set returns it beside the recording's five, and the
five are byte-unchanged. A write that quietly edited a replayed record would
look perfect in isolation.

**The linkage.** The published anchor's `manifest_uri` resolves per §7.5.2 and
the manifest's ref fetches as the committed bytes. This is the write-side twin
of the suite's §8.2, which does the same from the read side.

**Rejections, including one that had no data before.** The authority check's
anchor leg was UNEXERCISED until the 1.8 revision gave `GeoAnchor` a URI to
carry: an anchor naming a foreign authority is now a 403 like any other record.

Usage:  gates/publish_anchor.py
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys

import jsonschema                                              # noqa: E402

from publish_common import (ROOT, aiohttp, authority_base, delete, get,  # noqa: E402
                            put, resolve_path, seeded)
from harness import Gateway, report                            # noqa: E402
from spatialdds_web import gateway, webjson                    # noqa: E402

DUCK = ROOT / "samples" / "duck.input.json"


async def main() -> int:
    d = json.loads(DUCK.read_text())
    anc, man = d["anchor"], d["manifest"]
    base = authority_base(d)
    checks: list[tuple[str, bool, str]] = []
    st = seeded(d)
    recorded = {k: i.data for k, i in st.latched[
        f"spatialdds{anc['path']}"].items()}
    gw = await Gateway(st, advertise=base, publish=True).start()
    url = f'{anc["path"]}/{anc["key"].replace("/", "%2F")}'

    async with aiohttp.ClientSession() as s:
        stm, _h, _b = await put(s, gw.base, gateway.RESOLVE_PATH, man)
        sta, ha, ba = await put(s, gw.base, url, anc["record"])
        got = json.loads(ba) if ba else {}
        checks.append((f"the manifest and the anchor are accepted (201/201)",
                       (stm, sta) == (201, 201),
                       f"got {stm}/{sta}: {ba[:150]!r}"))
        checks.append(("the response is the authored record, field for field",
                       got == anc["record"],
                       f"differs at {sorted(k for k in set(got) | set(anc['record']) if got.get(k) != anc['record'].get(k))}"))
        checks.append(("its ETag is the digest of its canonical form",
                       ha.get("ETag") == f'"sha256:{webjson.digest(anc["record"])}"',
                       f"{ha.get('ETag')!r}"))
        try:
            jsonschema.validate(got, webjson.schemas()[anc["fqn"]])
            ok, why = True, ""
        except jsonschema.ValidationError as e:
            ok, why = False, e.message[:120]
        checks.append((f"it validates against the vendored {anc['fqn']} "
                       f"schema", ok, why))
        # The claim level, checked rather than asserted in prose: confidence
        # must be the monotone summary of cov that GeoAnchor asks for, or the
        # record contradicts itself and "method is the claim level" is empty.
        sig = max(got["cov"]["pos"][0], got["cov"]["pos"][4],
                  got["cov"]["pos"][8]) ** 0.5
        checks.append((f"confidence {got['confidence']} is 1/(1+sigma_max) "
                       f"for sigma_max {sig:.2f} m, so the scalar and the "
                       f"matrix do not contradict",
                       abs(got["confidence"] - 1.0 / (1.0 + sig)) < 5e-5,
                       f"{got['confidence']} vs {1.0 / (1.0 + sig):.4f}"))

        # Siblings.
        _st, _h, bl = await get(s, gw.base, anc["path"])
        served = {x["anchor_id"]: x for x in json.loads(bl)}
        checks.append((f"the latched set is the recording's {len(recorded)} "
                       f"plus the published one ({len(served)})",
                       set(served) == set(recorded) | {anc["key"]},
                       f"{sorted(served)}"))
        changed = [k for k, v in recorded.items() if served.get(k) != v]
        checks.append((f"and the recording's anchors are byte-unchanged",
                       not changed, f"changed: {changed}"))

        # The linkage, end to end.
        stn, hn, bn = await get(s, gw.base,
                                resolve_path(anc["record"]["manifest_uri"]))
        doc = json.loads(bn) if bn else {}
        checks.append(("the published anchor's manifest_uri resolves (200)",
                       stn == 200, f"got {stn}"))
        checks.append(("to the manifest the anchor names", doc == man,
                       "differs"))
        committed = (ROOT / d["asset"]["path"]).read_bytes()
        refp = "/" + doc.get("assets", [{}])[0].get("uri", "").split("/", 3)[-1]
        stb, _hb, raw = await get(s, gw.base, refp)
        checks.append((f"and its ref fetches the committed asset byte for "
                       f"byte ({len(committed)} bytes)",
                       stb == 200 and raw == committed,
                       f"got {stb}, {len(raw)} bytes"))

        # Rejections, for the new kind.
        st4, _h, _b = await put(s, gw.base, url, anc["record"], token=None)
        checks.append(("an anchor publish without a token is 401",
                       st4 == 401, f"got {st4}"))
        st5, _h, b5 = await put(s, gw.base, url, {"anchor_id": anc["key"]})
        checks.append(("a body that is not an anchor is 400 schema-invalid",
                       st5 == 400
                       and json.loads(b5).get("type", "").endswith(
                           "schema-invalid"), f"got {st5}"))
        foreign = copy.deepcopy(anc["record"])
        foreign["manifest_uri"] = \
            "spatialdds://someone.else.example/z/content/q"
        st6, _h, b6 = await put(s, gw.base, url, foreign)
        checks.append(("an anchor naming a foreign authority is 403 -- the "
                       "leg that had no data to exercise it before the "
                       "revision",
                       st6 == 403 and json.loads(b6).get("type", "").endswith(
                           "foreign-authority"), f"got {st6}"))
        _st, _h, bl2 = await get(s, gw.base, anc["path"])
        checks.append(("and after three refusals the latched set is unchanged",
                       {x["anchor_id"] for x in json.loads(bl2)} == set(served),
                       "a refusal changed the registry"))

        # Dispose, and the recording's own anchors survive it.
        std, _h, _b = await delete(s, gw.base, url)
        _st, _h, bl3 = await get(s, gw.base, anc["path"])
        after = {x["anchor_id"] for x in json.loads(bl3)}
        checks.append((f"disposing the published anchor leaves the "
                       f"recording's {len(recorded)} (204)",
                       std == 204 and after == set(recorded), f"{sorted(after)}"))

    await gw.stop()
    return report("publish anchor", checks,
                  "a published anchor is an anchor, and the content it names "
                  "resolves")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
