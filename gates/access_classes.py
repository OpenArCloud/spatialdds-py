#!/usr/bin/env python3
"""Configuration is public and cacheable; observation is gated and is not.

Tested in both directions, because a one-directional check is how access
control quietly stops working: that the token works proves the lock turns,
that its absence is refused proves there is a lock.

The structural invariant matters more than either: an operator-scoped resource
must never be served cacheable, and must never be CORS-open. A bearer-gated
body with `max-age` on it sits in a shared cache, where the next request
without a token can be served it; one with `Access-Control-Allow-Origin: *` on
it is readable by script on any page the operator never heard of.

The public half is checked positively as well, because the browser examples in
`examples/browser.md` claim cross-origin reads work. A header that is served
but not exposed is the same defect in smaller form: the reader can see the
body and not the ETag or the access class describing it, so
`Access-Control-Expose-Headers` is checked, and so is the 304, which is the
response a cache revalidation actually gets.

Usage:  gates/access_classes.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402
import websockets                                              # noqa: E402

from harness import Gateway, TOKEN, report, state              # noqa: E402
from spatialdds_web import gateway                             # noqa: E402


async def run() -> int:
    st = state()
    gw = await Gateway(st).start()
    checks: list[tuple[str, bool, str]] = []
    npub = nop = 0

    async with aiohttp.ClientSession() as s:
        for path in sorted(st.resources):
            res = st.resources[path]
            label = f"{res.scene[:8]}/{res.type_seg}"
            async with s.get(gw.base + path) as r:
                anon, acc = r.status, r.headers.get("Cache-Control", "")
                wa = r.headers.get("WWW-Authenticate", "")
                aco = r.headers.get("Access-Control-Allow-Origin", "")
                exp = r.headers.get("Access-Control-Expose-Headers", "")
                etag = r.headers.get("ETag", "")
            async with s.get(gw.base + path,
                             headers={"Authorization": f"Bearer {TOKEN}"}) as r:
                tok, tcc = r.status, r.headers.get("Cache-Control", "")
            async with s.get(gw.base + path,
                             headers={"Authorization": "Bearer wrong"}) as r:
                wrong = r.status
            if res.access == gateway.PUBLIC:
                npub += 1
                checks.append((f"{label}: public, served without auth",
                               anon == 200, f"got {anon}"))
                checks.append((f"{label}: public, cacheable",
                               "max-age" in acc and "public" in acc, acc))
                checks.append((f"{label}: public, CORS-open",
                               aco == "*", f"ACAO {aco!r}"))
                checks.append((f"{label}: its ETag and access class are "
                               f"readable cross-origin",
                               "ETag" in exp
                               and "X-SpatialDDS-Access" in exp,
                               f"expose {exp!r}"))
                async with s.get(gw.base + path,
                                 headers={"If-None-Match": etag}) as r:
                    nm, nm_aco = r.status, r.headers.get(
                        "Access-Control-Allow-Origin", "")
                checks.append((f"{label}: the 304 is CORS-open too",
                               nm == 304 and nm_aco == "*",
                               f"got {nm}, ACAO {nm_aco!r}"))
            else:
                nop += 1
                checks.append((f"{label}: refused without a token",
                               anon == 401, f"got {anon}"))
                checks.append((f"{label}: refused with a wrong token",
                               wrong == 401, f"got {wrong}"))
                checks.append((f"{label}: served with the right token",
                               tok == 200, f"got {tok}"))
                checks.append((f"{label}: the refusal says how to authenticate",
                               wa.startswith("Bearer"), wa))
                checks.append((f"{label}: never cacheable, either way",
                               "no-store" in acc and "no-store" in tcc,
                               f"{acc!r}/{tcc!r}"))
                checks.append((f"{label}: never CORS-open",
                               aco == "", f"ACAO {aco!r}"))

        contra: list[str] = []
        cors_contra: list[str] = []
        for path, res in sorted(st.resources.items()):
            if res.access != gateway.OPERATOR:
                continue
            async with s.get(gw.base + path,
                             headers={"Authorization": f"Bearer {TOKEN}"}) as r:
                cc = r.headers.get("Cache-Control", "")
                co = r.headers.get("Access-Control-Allow-Origin", "")
            if "max-age" in cc or "public" in cc:
                contra.append(f"{path} -> {cc}")
            if co:
                cors_contra.append(f"{path} -> {co}")
        checks.append(("no operator-scoped resource is served cacheable",
                       not contra, f"{contra[:2]}"))
        checks.append(("no operator-scoped resource is served CORS-open",
                       not cors_contra, f"{cors_contra[:2]}"))

    op = next((p for p, r in sorted(st.resources.items())
               if r.access == gateway.OPERATOR and r.latched), None)
    if op:
        url = gw.base.replace("http://", "ws://") + op
        refused = False
        try:
            async with websockets.connect(url) as c:
                await asyncio.wait_for(c.recv(), 5)
        except Exception:
            refused = True
        allowed = False
        try:
            async with websockets.connect(
                    url, additional_headers={
                        "Authorization": f"Bearer {TOKEN}"}) as c:
                json.loads(await asyncio.wait_for(c.recv(), 10))
                allowed = True
        except Exception:
            allowed = False
        checks.append(("a subscription to a gated topic is refused without a "
                       "token", refused, "the stream was served anonymously"))
        checks.append(("the same subscription succeeds with the token",
                       allowed, "the token was rejected"))

    # Appendix N.8: errors are RFC 7807 problem documents. Checked here
    # because a refusal is the error this endpoint raises most often, and a
    # body nothing reads is a body with no coverage.
    async with aiohttp.ClientSession() as s:
        op_path = next((p for p, r in sorted(st.resources.items())
                        if r.access == gateway.OPERATOR), None)
        async with s.get(gw.base + op_path) as r:
            ctype = r.headers.get("Content-Type", "")
            body = await r.read()
        doc = json.loads(body)
        checks.append(("a refusal is served as application/problem+json",
                       ctype.startswith("application/problem+json"),
                       f"Content-Type: {ctype!r}"))
        missing = [k for k in ("type", "title", "status", "detail")
                   if k not in doc]
        checks.append(("the problem document carries type, title, status and "
                       "detail", not missing, f"missing {missing}"))
        checks.append((f"status in the body matches the status line "
                       f"({doc.get('status')})", doc.get("status") == 401,
                       f"body says {doc.get('status')}, line said 401"))
        checks.append(("no deprecated ad-hoc error member survives",
                       "error" not in doc, f"keys {sorted(doc)}"))
        async with s.get(gw.base + "/no/such/topic/v1") as r:
            nf_ctype = r.headers.get("Content-Type", "")
            nf = json.loads(await r.read())
        checks.append(("a 404 is a problem document too",
                       nf_ctype.startswith("application/problem+json")
                       and nf.get("status") == 404,
                       f"{nf_ctype!r}, status {nf.get('status')}"))

    await gw.stop()
    checks.append((f"both classes are present ({npub} public, {nop} gated)",
                   npub > 0 and nop > 0, "one class is unrepresented"))
    return report("access classes", checks,
                  "configuration is public, observation is gated")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
