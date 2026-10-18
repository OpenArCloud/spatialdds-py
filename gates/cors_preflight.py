#!/usr/bin/env python3
"""The preflight a credentialed browser request needs, and the origin on the
answer it earned.

`Authorization` is not a CORS-safelisted request header, so a cross-origin
request carrying one is preceded by an `OPTIONS` preflight. This gateway routed
no `OPTIONS`, so aiohttp answered `405 Allow: GET,HEAD,POST` and the browser
stopped there --- never making the request. And an operator-scoped response
carried no `Access-Control-Allow-Origin` at all, so even a page holding a valid
token could not read a word of the `200` it had earned.

Both were measured from outside, by `SpatialDDS-demo`'s browser lane, against
0.5.2. Neither is a specification matter: nothing in Appendix N forbade any of
it, which is exactly the separate gap filed for the spec --- a binding whose
flagship client is a browser should have to *state* its preflight behaviour.

**What this gate can and cannot do, stated because it decides the assertions.**
`aiohttp` enforces no CORS: it will happily read a response with no
`Access-Control-Allow-Origin` and send an `Authorization` header with no
preflight at all. So no check here can observe "a browser could read this".
What it can do is assert, header by header, the things a browser consults
before deciding --- the preflight's status and its `Allow-*` set, and the
origin and `Vary` on the real response. That is the whole contract, and it is
checkable without a browser as long as nobody mistakes the one for the other.

The negative matters as much as the positive: `Allow-Credentials` must be
**absent**, because it is the header that invites cookies and HTTP auth to ride
along ambiently. The point of this change is a credential the page holds and
chooses to attach, not one the browser supplies on its behalf.

Usage:  gates/cors_preflight.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

import harness                                                 # noqa: E402
from spatialdds.server import gateway                          # noqa: E402

#: The page's origin, in the shape a dev server actually has: a different port
#: on the same host, which is still cross-origin and is the configuration that
#: found all of this.
ORIGIN = "http://localhost:5173"


def _pick(st: gateway.State, access: str) -> str:
    for path in sorted(st.resources):
        if st.resources[path].access == access:
            return path
    raise AssertionError(f"the fixture holds no {access} resource")


async def _run() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    st = harness.state()
    public = _pick(st, gateway.PUBLIC)
    gated = _pick(st, gateway.OPERATOR)

    async with harness.Gateway(st) as gw:
        async with aiohttp.ClientSession() as s:
            # --- the preflight a credentialed GET forces
            async with s.options(gw.base + gated, headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization"}) as r:
                status, h = r.status, dict(r.headers)
            checks.append((
                "a preflight carrying `authorization` is answered, not 405ed",
                200 <= status < 300,
                f"OPTIONS -> {status} (was 405 Allow: GET,HEAD,POST)"))
            checks.append((
                "...and names the asking origin rather than a wildcard",
                h.get("Access-Control-Allow-Origin") == ORIGIN,
                f"Access-Control-Allow-Origin: "
                f"{h.get('Access-Control-Allow-Origin')!r}"))
            checks.append((
                "...and permits the header that forced it",
                "authorization" in
                (h.get("Access-Control-Allow-Headers") or "").lower(),
                f"Access-Control-Allow-Headers: "
                f"{h.get('Access-Control-Allow-Headers')!r}"))
            checks.append((
                "...and varies on what the answer depends on",
                "origin" in (h.get("Vary") or "").lower(),
                f"Vary: {h.get('Vary')!r}"))
            checks.append((
                "...and does not invite ambient credentials",
                "Access-Control-Allow-Credentials" not in h,
                "Access-Control-Allow-Credentials absent: "
                f"{'Access-Control-Allow-Credentials' not in h}"))

            # --- N.6's preflight, which is the one that blocked commanding
            async with s.options(gw.base + public, headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers":
                        "authorization,content-type"}) as r:
                post_status, post_h = r.status, dict(r.headers)
            allow = (post_h.get("Access-Control-Allow-Methods") or "").upper()
            checks.append((
                "a command POST's preflight is answered and POST permitted",
                200 <= post_status < 300 and "POST" in allow,
                f"OPTIONS -> {post_status}, "
                f"Access-Control-Allow-Methods: {allow!r}"))
            checks.append((
                "...with both headers a JSON command sends",
                all(x in (post_h.get("Access-Control-Allow-Headers") or "")
                    .lower() for x in ("authorization", "content-type")),
                f"Access-Control-Allow-Headers: "
                f"{post_h.get('Access-Control-Allow-Headers')!r}"))

            # --- the authorized response is now readable by its own client
            async with s.get(gw.base + gated, headers={
                    "Origin": ORIGIN,
                    "Authorization": f"Bearer {gw.token}"}) as r:
                ok_status, ok_h = r.status, dict(r.headers)
            checks.append((
                "an authorized operator-scoped GET carries the asking origin",
                ok_status == 200
                and ok_h.get("Access-Control-Allow-Origin") == ORIGIN,
                f"GET -> {ok_status}, Access-Control-Allow-Origin: "
                f"{ok_h.get('Access-Control-Allow-Origin')!r}"))
            checks.append((
                "...and still refuses to be cached, and varies on both",
                ok_h.get("Cache-Control") == "no-store"
                and {"origin", "authorization"} <= {
                    p.strip().lower() for p in (ok_h.get("Vary") or "").split(",")},
                f"Cache-Control: {ok_h.get('Cache-Control')!r}, "
                f"Vary: {ok_h.get('Vary')!r}"))
            checks.append((
                "...and exposes the headers that describe it",
                "etag" in (ok_h.get("Access-Control-Expose-Headers")
                           or "").lower(),
                f"Access-Control-Expose-Headers: "
                f"{ok_h.get('Access-Control-Expose-Headers')!r}"))
            checks.append((
                "...and no ambient-credential invitation here either",
                "Access-Control-Allow-Credentials" not in ok_h,
                "Access-Control-Allow-Credentials absent: "
                f"{'Access-Control-Allow-Credentials' not in ok_h}"))

            # --- the gate is not weakened: no credential is still a refusal
            async with s.get(gw.base + gated,
                             headers={"Origin": ORIGIN}) as r:
                refused = r.status
            checks.append((
                "an unauthorized request is still refused, origin or not",
                refused == 401,
                f"GET without a credential -> {refused}"))

            # --- and the public branch is untouched
            async with s.get(gw.base + public,
                             headers={"Origin": ORIGIN}) as r:
                pub_h = dict(r.headers)
            checks.append((
                "a public-cacheable resource still answers any origin with `*`",
                pub_h.get("Access-Control-Allow-Origin") == "*"
                and pub_h.get("Cache-Control", "").startswith("public"),
                f"Access-Control-Allow-Origin: "
                f"{pub_h.get('Access-Control-Allow-Origin')!r}, "
                f"Cache-Control: {pub_h.get('Cache-Control')!r}"))

            # A credential volunteered at a public resource: the preflight that
            # forces must still be answered, which is the other half of the
            # defect a client hit by being defensive.
            async with s.options(gw.base + public, headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization"}) as r:
                vol = r.status
            checks.append((
                "a credential volunteered at a public resource preflights "
                "successfully too",
                200 <= vol < 300,
                f"OPTIONS -> {vol}; a client that sends a credential it does "
                f"not need is no longer refused before it asks"))

            # --- every answer, not just the ones built by _cache_headers
            #
            # 0.5.3 threaded the origin through the three call sites that
            # construct a response from `_cache_headers` and missed the two
            # that do not. These rows are that miss, turned into checks: the
            # command lane's reply, and an N.8 problem document, which is the
            # worse of the two --- N.8 exists to hand a client a *reason*, and
            # a reason a cross-origin browser cannot read is the surface
            # failing at the one thing that distinguishes it from a status.
            async with s.post(gw.base + public, json={}, headers={
                    "Origin": ORIGIN,
                    "Authorization": f"Bearer {gw.token}"}) as r:
                cmd_status, cmd_h = r.status, dict(r.headers)
            checks.append((
                "the command lane's own reply names the asking origin",
                cmd_h.get("Access-Control-Allow-Origin") == ORIGIN,
                f"POST -> {cmd_status}, Access-Control-Allow-Origin: "
                f"{cmd_h.get('Access-Control-Allow-Origin')!r}"))

            async with s.get(gw.base + gated,
                             headers={"Origin": ORIGIN}) as r:
                prob_status, prob_h = r.status, dict(r.headers)
                prob_type = r.headers.get("Content-Type", "")
            checks.append((
                "an N.8 problem document is readable by the client it is for",
                prob_status == 401
                and prob_h.get("Access-Control-Allow-Origin") == ORIGIN
                and "problem" in prob_type,
                f"{prob_status} {prob_type}, Access-Control-Allow-Origin: "
                f"{prob_h.get('Access-Control-Allow-Origin')!r}"))

            # A 304 too: a conditional GET is the common case for a public
            # resource and its headers are built on a different branch again.
            async with s.get(gw.base + public,
                             headers={"Origin": ORIGIN}) as r:
                etag = r.headers.get("ETag", "")
            async with s.get(gw.base + public, headers={
                    "Origin": ORIGIN, "If-None-Match": etag}) as r:
                nm_status, nm_h = r.status, dict(r.headers)
            checks.append((
                "a 304 revalidation still names an origin",
                nm_status == 304
                and nm_h.get("Access-Control-Allow-Origin") is not None,
                f"{nm_status}, Access-Control-Allow-Origin: "
                f"{nm_h.get('Access-Control-Allow-Origin')!r}"))

            # --- the routes mirror the surface rather than catching all
            #
            # Two segments, deliberately. The first version of this check used
            # `/nothing/here/at/all`, which *looks* unserved and is not: four
            # segments match `/{scene}/{stream}/{type}/{version}`, so a 204
            # there is correct and the check was aimed at nothing. A preflight
            # on a well-formed path whose resource does not exist should
            # succeed and let the GET answer 404 --- which is the row below.
            async with s.options(gw.base + "/nothing/here",
                                 headers={"Origin": ORIGIN}) as r:
                missing = r.status
            checks.append((
                "a path shape the gateway does not route gets no preflight",
                missing == 404,
                f"OPTIONS on an unrouted path shape -> {missing}"))

            # And the pattern-matching case: the preflight succeeds, then the
            # request it was for is the thing that 404s. That is the right
            # division --- CORS decides whether the client may ask, not
            # whether the answer exists.
            unreal = "/no-such-scene/no-such-stream/no_such_type/v1"
            async with s.options(gw.base + unreal, headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "GET"}) as r:
                pre = r.status
            async with s.get(gw.base + unreal,
                             headers={"Origin": ORIGIN}) as r:
                got = r.status
            checks.append((
                "a routed path with no such resource preflights, then 404s",
                200 <= pre < 300 and got == 404,
                f"OPTIONS -> {pre}, GET -> {got}"))
    return checks


def main() -> int:
    checks = asyncio.run(_run())
    ok = all(c[1] for c in checks)
    return harness.report(
        "the CORS preflight, and the origin on a response a credential earned",
        checks,
        "A browser can now reach this gateway: the preflight an "
        "`Authorization` header forces is answered and names the asking "
        "origin, an authorized operator-scoped response carries that origin "
        "while staying `no-store` and varying on both, and no gate is "
        "weakened --- a request without a credential is still a 401. "
        "`Allow-Credentials` is absent throughout, which is the difference "
        "between a credential a page chooses to attach and one the browser "
        "attaches for it. Note what no check here can claim: aiohttp enforces "
        "no CORS, so these assert the headers a browser consults, not the "
        "browser's decision."
        if ok else
        "A cross-origin browser cannot use this gateway as it stands.")


if __name__ == "__main__":
    sys.exit(main())
