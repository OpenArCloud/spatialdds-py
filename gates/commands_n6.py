#!/usr/bin/env python3
"""The N.6 command surface, including the clause no conformance run has taken.

Six claims, and the sixth is the reason this gate exists rather than relying
on the suite:

**Accepted.** A valid command on a lane with a handler is `202`, the handler
receives the lifted record, and the response names the outcome. Acceptance and
not completion: N.6 requires no completion tracking and this gateway invents
none.

**Declined, with a reason on the wire.** M.1 makes a decline "a first-class,
observable outcome, not an absence", so a refusal is `409` with the reason in
an N.8 problem document --- not a `200` whose body a status-only caller never
reads. A decline that `response.ok` swallows is the absence M.1 forbids.

**Refused where there is nothing to accept.** A served-but-read-only lane and
an unserved path both answer `405` per N.6, with an N.8 body naming what the
endpoint does accept, so the absence is stated rather than inferred from a
framework's default page.

**Operator-scoped, per N.4.** No credential is `401` with
`WWW-Authenticate: Bearer` and `no-store`, and the refusal for a lane with no
handler comes *before* the auth check --- a `401` there would tell an
anonymous caller that a lane exists, which is information this endpoint does
not have.

**Validated before delivered.** A body that fails the published schema is
`400` and the handler is never called. A backend must not be handed something
the binding would not serve.

**Advertised, and the advertisement is load-bearing.** A client discovers the
command lane from the manifest rather than by POSTing and reading the
refusal. N.7 has no member for this, so the member is this repository's own
(`commands.ADVERTISEMENT`) and is checked here to appear on exactly the lanes
with handlers and on no others --- `docs/WIRE.md` records it as
implementation-defined and the 1.9 note files the gap.

**And the injection the conformance suite cannot do.** The suite's command
check POSTs to `discover(ep)[0]` --- the first advertised topic by position.
Measured on 2026-10-17 against this very surface: the check reported
`POST -> 405 (no command lane advertised)` and passed, while the command lane
sat third in the manifest with a working handler on it. So a correct N.6
implementation moves the suite not at all, and an endpoint that advertised a
lane and then refused it would pass identically. This gate asserts what the
suite structurally cannot: that the advertisement and the behaviour agree.

**And the gap this gate had in its own first version, which is the lesson
worth more than the check.** It built its registry and registered handlers
*before* calling `make_app` — and that is the one order an embedder cannot
use, because the app must exist before the backend that supplies the handlers
can be built. So the gate exercised the seam in an order no audience could
hold it in, and `make_app` silently discarding an empty registry passed green
through every run. The check-environment-versus-ship-environment lesson, in
miniature, inside an API: the gate was not blind to a case, it was holding the
object the convenient way round.

The register-after check below closes exactly that gap, and the rule it
generalises to is worth stating: when a seam has an order, exercise the order
the caller is forced into, not the order that is easy to write in a test.

Usage:  gates/commands_n6.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

import harness                                                 # noqa: E402
from spatialdds.server import commands                         # noqa: E402

ANCHOR_FQN = "spatial.core.GeoAnchor"
ANNOUNCE_FQN = "spatial.disco.Announce"


def _registry(seen: list):
    reg = commands.Registry()

    def handler(record):
        seen.append(record)
        if record.get("method") == "NO":
            return commands.Outcome.decline("declined by the gate's handler")
        return commands.Outcome.accept()

    reg.register(ANCHOR_FQN, handler)
    return reg


async def _run() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    seen: list = []
    reg = _registry(seen)

    # The fountain state, not the default retail one: `geo_anchor` is
    # the lane `--demo` carries a handler for, so this gate and the
    # shipped demo exercise the same type.
    async with harness.Gateway(harness.state(harness.FOUNTAIN),
                               commands=reg) as gw:
        async with aiohttp.ClientSession() as s:
            auth = {"Authorization": f"Bearer {gw.token}"}

            # Find the lanes by advertisement, which is the whole point.
            async with s.get(f"{gw.base}/.well-known/spatialdds/search",
                             headers=auth) as r:
                doc = await r.json()
            topics = [t for m in doc["results"]
                      for t in m["service"]["topics"]]
            advertised = [t for t in topics
                          if t.get(commands.ADVERTISEMENT)]
            others = [t for t in topics if not t.get(commands.ADVERTISEMENT)]

            checks.append((
                "exactly the handled lane advertises commands",
                len(advertised) == 1 and advertised[0]["type"] == "geo_anchor",
                f"{[t['type'] for t in advertised]} advertise, "
                f"{[t['type'] for t in others]} do not"))

            lane = gw.connect(advertised[0]["url"]) if advertised else None
            if lane is None:
                return checks
            quiet = gw.connect(next(t["url"] for t in others))

            # A real record off the lane, so the body is one the binding serves.
            async with s.get(lane, headers=auth) as r:
                body = (await r.json())[0]

            # 1. accepted
            async with s.post(lane, headers=auth, json=body) as r:
                code, out = r.status, await r.json()
            checks.append((
                "a valid command on a handled lane is 202 accepted",
                code == 202 and out.get("outcome") == "accepted",
                f"{code} {out.get('outcome')}"))
            checks.append((
                "the handler received the lifted record",
                len(seen) == 1 and seen[0].get("anchor_id") == body.get("anchor_id"),
                f"{len(seen)} delivered, keys match: "
                f"{bool(seen) and seen[0].get('anchor_id') == body.get('anchor_id')}"))

            # 2. declined -- 409, reason on the wire, problem+json
            declined = dict(body, method="NO")
            async with s.post(lane, headers=auth, json=declined) as r:
                code, ctype, out = r.status, r.content_type, await r.json()
            checks.append((
                "a declined command is 409 problem+json carrying M.1's reason",
                code == 409 and ctype == "application/problem+json"
                and "declined by the gate's handler" in out.get("detail", ""),
                f"{code} {ctype} detail={out.get('detail', '')[:34]!r}"))

            # 3. a served lane with no handler, and an unserved path
            async with s.post(quiet, headers=auth, json={}) as r:
                quiet_code, quiet_doc = r.status, await r.json()
            async with s.post(f"{gw.base}/no/such/thing/v1",
                              headers=auth, json={}) as r:
                missing_code = r.status
            checks.append((
                "a read-only lane and an unserved path both refuse with 405",
                quiet_code == 405 and missing_code == 405,
                f"read-only {quiet_code}, unserved {missing_code}"))
            checks.append((
                "the 405 names what the endpoint does accept",
                quiet_doc.get("accepts") == [ANCHOR_FQN],
                f"accepts={quiet_doc.get('accepts')}"))

            # 4. N.4: no credential
            async with s.post(lane, json=body) as r:
                code = r.status
                www = r.headers.get("WWW-Authenticate", "")
                cache = r.headers.get("Cache-Control", "")
            checks.append((
                "no credential is 401 with Bearer challenge and no-store",
                code == 401 and www.startswith("Bearer")
                and cache == "no-store",
                f"{code} {www!r} {cache!r}"))
            # ...and that a handler-less lane refuses before authenticating,
            # so an anonymous caller learns nothing about which lanes exist.
            async with s.post(quiet, json={}) as r:
                code = r.status
            checks.append((
                "a handler-less lane refuses before it authenticates",
                code == 405,
                f"unauthenticated POST to a read-only lane -> {code} "
                f"(401 would confirm the lane exists)"))

            # 5. validated before delivered
            before = len(seen)
            async with s.post(lane, headers=auth,
                              json={"nonsense": True}) as r:
                code, ctype = r.status, r.content_type
            checks.append((
                "an invalid body is 400 and never reaches the handler",
                code == 400 and ctype == "application/problem+json"
                and len(seen) == before,
                f"{code} {ctype}, handler calls {before} -> {len(seen)}"))

    # 6. The registry an embedder passes is the registry the app uses, even
    # when it is empty at the time. This is the shape every embedder has:
    # the app has to exist before the backend that supplies the handlers can
    # be built, so the registry is passed empty and populated a few lines
    # later. `make_app` had `command_registry or Registry()` and `Registry`
    # was falsy when empty, so that registry was silently replaced and every
    # later `register()` went to a discarded object -- the web bridge's
    # command lane advertised nothing and refused every POST while holding
    # the handler. Asserted on identity *and* on behaviour: identity alone
    # would pass if someone copied the handlers across, and behaviour alone
    # would not say which object won.
    late = commands.Registry()
    async with harness.Gateway(harness.state(harness.FOUNTAIN),
                               commands=late) as gw:
        same = gw.app["commands"] is late
        late.register(ANCHOR_FQN, lambda record: commands.Outcome.accept())
        async with aiohttp.ClientSession() as s:
            auth = {"Authorization": f"Bearer {gw.token}"}
            async with s.get(f"{gw.base}/.well-known/spatialdds/search",
                             headers=auth) as r:
                doc = await r.json()
            adv = [t for m in doc["results"] for t in m["service"]["topics"]
                   if t.get(commands.ADVERTISEMENT)]
        checks.append((
            "a registry passed empty and filled later is the one the app uses",
            same and len(adv) == 1,
            f"identity kept={same}, advertised after late register={len(adv)}"))

    # 7. the injection: an empty registry must advertise nothing and refuse
    # everything, so "advertises a lane" and "serves one" cannot drift.
    async with harness.Gateway(harness.state(harness.FOUNTAIN)) as gw:
        async with aiohttp.ClientSession() as s:
            auth = {"Authorization": f"Bearer {gw.token}"}
            async with s.get(f"{gw.base}/.well-known/spatialdds/search",
                             headers=auth) as r:
                doc = await r.json()
            adv = [t for m in doc["results"] for t in m["service"]["topics"]
                   if t.get(commands.ADVERTISEMENT)]
            lane = next(t["url"] for m in doc["results"]
                        for t in m["service"]["topics"])
            async with s.post(gw.connect(lane), headers=auth, json={}) as r:
                code = r.status
            checks.append((
                "with no handlers: nothing advertised and every POST refused",
                not adv and code == 405,
                f"{len(adv)} advertised, POST -> {code}"))
    return checks


def main() -> int:
    checks = asyncio.run(_run())
    ok = all(c[1] for c in checks)
    return harness.report(
        "N.6 commands: a gateway surface a backend supplies",
        checks,
        "The command surface accepts, declines with a reason, refuses where "
        "there is nothing to accept, and advertises exactly the lanes it "
        "serves. The last check is the one the conformance suite cannot make: "
        "its command check POSTs to the first advertised topic by position, "
        "so it passed on the 405 branch against this very surface while a "
        "working handler sat on the third lane."
        if ok else
        "The N.6 surface does not behave as Appendix N.6 and M.1 require.")


if __name__ == "__main__":
    sys.exit(main())
