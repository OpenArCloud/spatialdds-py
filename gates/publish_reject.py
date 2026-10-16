#!/usr/bin/env python3
"""Every rejection class, exercised, and each one a sentence.

Validation happens at the door: nothing is stored before the body has passed
its schema, its authority check, and the wire. So each refusal below is also a
check that the registry is unchanged afterwards -- a surface that rejected a
record and kept it would be worse than one that accepted it.

Usage:  gates/publish_reject.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys

from publish_common import (ROOT, aiohttp, authored, authority_base,     # noqa: E402
                            delete, get, put, seeded)
from harness import Gateway, report                            # noqa: E402
from spatialdds_web import gateway                             # noqa: E402

PROBLEM = "application/problem+json"


async def main() -> int:
    doc = authored()
    base = authority_base(doc)
    spec = doc["service"]
    url = f"{spec['path']}/{spec['key']}"
    checks: list[tuple[str, bool, str]] = []

    gw = await Gateway(seeded(doc), advertise=base, publish=True).start()
    async with aiohttp.ClientSession() as s:
        async def expect(label, status, slug, body, token="gate-token-not-a-secret",
                         method="put", path=url, headers=None):
            fn = put if method == "put" else delete
            args = (s, gw.base, path, body) if method == "put" else (s, gw.base, path)
            st_, h, raw = await fn(*args, token=token, **({"headers": headers}
                                                         if method == "put" else {}))
            d = json.loads(raw) if raw else {}
            checks.append((f"{label}: {status}", st_ == status, f"got {st_}"))
            checks.append((f"{label}: problem+json naming it ({slug})",
                           h.get("Content-Type", "").startswith(PROBLEM)
                           and d.get("type", "").endswith(slug),
                           f"{h.get('Content-Type')!r} {d.get('type')!r}"))
            checks.append((f"{label}: the detail is a sentence",
                           isinstance(d.get("detail"), str)
                           and d["detail"].endswith(".")
                           and len(d["detail"].split()) >= 6,
                           f"{d.get('detail')!r}"))
            return d

        await expect("no token", 401, "operator-scoped", spec["record"],
                     token=None)
        await expect("a wrong token", 401, "operator-scoped", spec["record"],
                     token="not-the-token")
        await expect("a body that is not a record", 400, "schema-invalid",
                     {"service_id": "x"})
        foreign = copy.deepcopy(spec["record"])
        foreign["manifest_uri"] = "spatialdds://someone.else.example/z/service/q"
        await expect("another authority's URI", 403, "foreign-authority",
                     foreign)
        # A topic that is genuinely read-only. This probe used to aim at
        # geo_anchor, which stopped being the right target the moment anchors
        # became publishable -- it then returned 400 for a bad body instead of
        # 405 for a forbidden topic, which is a different claim. Zones have no
        # write surface and are not about to get one.
        await expect("a topic that is not publishable", 405, "not-publishable",
                     spec["record"],
                     path="/ut-austin-south-mall/regions/spatial_zone/v1/x")
        mism = copy.deepcopy(spec["record"])
        mism["service_id"] = "not-the-url-key"
        await expect("a body whose key is not the URL's", 400, "key-mismatch",
                     mism)
        await expect("disposing something absent", 404, "no-such-instance",
                     None, method="delete",
                     path=f"{spec['path']}/never-published")

        # Nothing above may have been stored.
        st_, _, raw = await get(s, gw.base, spec["path"])
        ids = [i.get("service_id") for i in json.loads(raw)]
        checks.append((f"after seven refusals the registry is untouched "
                       f"({ids})",
                       doc["service"]["record"]["service_id"] not in ids
                       and "not-the-url-key" not in ids, f"{ids}"))
    await gw.stop()

    # An endpoint reachable only on loopback is an authority -- `127.0.0.1` --
    # just not the records' one, so these records are foreign to it. That is
    # the realistic misconfiguration and it is checked over HTTP.
    gw2 = await Gateway(seeded(doc), advertise=None, publish=True).start()
    async with aiohttp.ClientSession() as s:
        st_, _h, raw = await put(s, gw2.base, url, spec["record"])
        d = json.loads(raw) if raw else {}
        checks.append(("an endpoint advertising loopback refuses records "
                       "belonging to another authority: 403",
                       st_ == 403
                       and d.get("type", "").endswith("foreign-authority"),
                       f"got {st_} {d.get('type')}"))
    await gw2.stop()

    # The genuinely authority-less case cannot be reached over HTTP, because a
    # bound endpoint always has a host. It is still the case that must not
    # pass, so it is checked where it lives, and the startup refusal that
    # keeps a deployment out of it is checked too.
    from spatialdds_web import publish as _pub
    try:
        _pub.check_authority("", spec["record"])
        got = "accepted"
    except _pub.Rejected as r:
        got = r.slug
    checks.append(("an authority check with nothing to check against refuses "
                   "rather than passing", got == "no-authority", f"{got}"))

    # And the reason the HTTP path cannot reach it: a bound endpoint always
    # has a host, so its authority is never empty. With no --public-url the
    # authority is `host:port`, which is a coherent namespace rather than an
    # absent one -- records naming it are accepted, records naming anything
    # else are foreign. So there is no state in which this check has nothing
    # to check; the guard above is for a programmatic caller passing no base.
    checks.append((f"a bound endpoint always has an authority to check "
                   f"against ({gateway.base_authority(gw2.base)!r})",
                   bool(gateway.base_authority(gw2.base)),
                   f"{gw2.base!r} yielded no authority"))

    return report("publish rejections", checks,
                  "each refusal is a status and a sentence, and stores nothing")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
