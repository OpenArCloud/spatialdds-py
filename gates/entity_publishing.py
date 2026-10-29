#!/usr/bin/env python3
"""A world from HTTP: entities and claims published, refreshed, lapsed, and on the bus.

0.8.0's write surface puts living things into a world, not only records
about one. The ruled policy (docs/PUBLISH.md, "World lanes"), held here:

* **A world from HTTP.** One declared entity, one observed entity with a
  refresh interval, and one claim between them, each PUT over HTTP, reach a
  WebSocket subscriber as ordinary samples and a DDS reader on another
  participant as the same: the reader's samples, projected to N.1, equal
  what the gateway serves.
* **The lapse, shown failable.** The observed entity's client stops
  re-PUTting. Inside the window after its interval the gateway withdraws it,
  a terminal sample then a dispose, "refresh lapsed", on the socket and on
  the bus. The declared entity, under the same silence, is still there.
  The injection arm stops the gateway's reaper and requires the same check
  to fail: the withdrawal is the mechanism's, not a timing accident.
* **Refresh works.** Re-PUTs inside a one-second interval keep an observed
  entity across several intervals. A gateway restarted on the journal serves
  the final state: what lapsed stays gone, what was refreshed is back, and
  the claim keeps its asserter.
* **The asserter is the principal.** A claim naming another asserter is
  refused with its problem document; an accepted one's asserter is the
  token's principal.
* **Basis is enforced.** An observed PUT with no interval is refused, and the
  refusal names the rule.

Usage:  gates/entity_publishing.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402

from harness import Gateway, blank, report                     # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.dds import codec                               # noqa: E402
from spatialdds.server import bus as bus_, gateway, tenancy    # noqa: E402

from _claim_type import CLAIM_IDL, Claim                       # noqa: E402

SCENE = "gate-world"
ENT = f"/{SCENE}/world/entity/v1"
CLM = f"/{SCENE}/world/gate.claim/v1"
FRAME = sd.frame("map/gate-world", convention="ENU", meters_per_unit=1.0)
ALICE, BOB = "tok-alice-entity-publishing", "tok-bob-entity-publishing"
REFRESH = 2.0
#: How late a lapse may be withdrawn and still be "within the window": the
#: reaper's period and a margin for a loaded machine.
SLACK = 1.0

EXT = sd.dds.extend("gate.claim", Claim, CLAIM_IDL)


def entity(eid: str, basis: str, x: float = 0.0) -> dict:
    return sd.entity(eid, frame_ref=FRAME, source_id="svc:gate:world",
                     position=[x, 0.0, 0.0], basis=basis)


def claim(cid: str, asserter: str = "") -> dict:
    return {"id": cid, "subject_ref": "ent:observed",
            "predicate": "https://example.org/predicate/near",
            "object_ref": "ent:declared", "basis": "DECLARED",
            "asserter": asserter, "stamp": sd.stamp(), "state": "CREATED",
            "state_reason": ""}


def tokens() -> tenancy.TokenTable:
    return tenancy.TokenTable([
        (tenancy.digest(ALICE), tenancy.Principal(
            "alice", "example.org", frozenset({SCENE}), may_create_scenes=True)),
        (tenancy.digest(BOB), tenancy.Principal(
            "bob", "example.org", frozenset({SCENE}), may_create_scenes=True)),
    ])


def fresh_state() -> gateway.State:
    st = blank()
    gateway.add_extension(st, EXT.type_name, EXT.fqn, EXT.record)
    return st


class Bus:
    """A DDS reader on its own participant, beside the gateway's bus."""

    def __init__(self, gw_bus: bus_.Bus) -> None:
        self.p = sd.participant("gate-reader", domain=gw_bus.domain)
        prof = gw_bus.profile
        self.readers = {
            "entity": self.p.reader("model_entity", qos_profile=prof,
                                    topic=f"spatialdds{ENT}"),
            "claim": self.p.reader(Claim, qos_profile=prof,
                                   topic=f"spatialdds{CLM}"),
        }
        self.events: list[tuple[float, str, str, str, dict | None, str]] = []

    def drain(self) -> None:
        for lane, rd in self.readers.items():
            for ev in rd.take():
                kind = type(ev).__name__
                if kind == "Sample":
                    doc = codec.to_json(ev.data)
                    key = doc.get("entity_id") or doc.get("id")
                    self.events.append((time.monotonic(), lane, "sample", key,
                                        doc, ""))
                elif kind == "Dispose":
                    self.events.append((time.monotonic(), lane, "dispose",
                                        ev.key, None, ev.reason or ""))

    def close(self) -> None:
        self.p.close()


async def socket_events(s, gw, path: str, out: list, stop: asyncio.Event):
    auth = {"Authorization": f"Bearer {ALICE}"}
    async with s.ws_connect(gw.base.replace("http", "ws", 1) + path,
                            headers=auth) as ws:
        while not stop.is_set():
            try:
                m = await ws.receive_json(timeout=0.2)
            except asyncio.TimeoutError:
                continue
            out.append((time.monotonic(), m))


def first(events, pred):
    return next((e for e in events if pred(e)), None)


async def journey(checks: list, journal: Path, domain: int,
                  inject_no_reaper: bool = False) -> dict:
    """One session against one gateway: the world, the lapse, the refresh."""
    st = fresh_state()
    gw_bus = bus_.Bus(domain)
    dds = Bus(gw_bus)
    gw = Gateway(st, publish=True, journal=journal, tokens=tokens(),
                 bus=gw_bus)
    found: dict = {}
    async with gw, aiohttp.ClientSession() as s:
        if inject_no_reaper:
            gw.app["reaper"].cancel()
        a = {"Authorization": f"Bearer {ALICE}"}

        # -- basis: an observed PUT with no interval, refused by its rule.
        r = await s.put(gw.base + ENT + "/ent:observed",
                        json=entity("ent:observed", "OBSERVED"), headers=a)
        found["no_interval"] = (r.status, await r.json())

        # -- the world. A lane exists once its first PUT creates it, so the
        # declared entity and the claim come first and the subscriber is
        # handed them as the lanes' latched replay; the observed entity
        # arrives live.
        r1 = await s.put(gw.base + ENT + "/ent:declared",
                         json=entity("ent:declared", "DECLARED"), headers=a)
        r3 = await s.put(gw.base + CLM + "/c1", json=claim("c1"), headers=a)
        ws: list = []
        stop = asyncio.Event()
        tasks = [asyncio.create_task(socket_events(s, gw, p, ws, stop))
                 for p in (ENT, CLM)]
        await asyncio.sleep(0.5)
        for t in tasks:
            if t.done():
                t.result()              # a refused subscription says so
        # Before the request: the lease starts when the gateway handles the
        # PUT, which is before its response arrives here, so a clock started
        # after the response reads every lapse a little early.
        t_obs = time.monotonic()
        r2 = await s.put(gw.base + ENT + f"/ent:observed?refresh={REFRESH:g}",
                         json=entity("ent:observed", "OBSERVED", 1.0),
                         headers=a)
        found["puts"] = [r1.status, r2.status, r3.status]
        found["served"] = {
            "ent:declared": await (await s.get(gw.base + ENT + "/ent:declared",
                                               headers=a)).json(),
            "ent:observed": await (await s.get(gw.base + ENT + "/ent:observed",
                                               headers=a)).json(),
            "c1": await (await s.get(gw.base + CLM + "/c1", headers=a)).json()}

        # -- the asserter: bob may not assert as alice, and alice as herself.
        r = await s.put(gw.base + CLM + "/c2", json=claim("c2", "alice"),
                        headers={"Authorization": f"Bearer {BOB}"})
        found["impostor"] = (r.status, await r.json())

        # -- refresh works: a one-second interval kept across four.
        t_kept = time.monotonic()
        for i in range(9):
            await s.put(gw.base + ENT + "/ent:kept?refresh=1",
                        json=entity("ent:kept", "OBSERVED", 2.0 + i * 0.1),
                        headers=a)
            await asyncio.sleep(0.5)
            dds.drain()
        found["kept_s"] = time.monotonic() - t_kept

        # -- the lapse: nobody refreshes ent:observed.
        deadline = t_obs + REFRESH + SLACK + 1.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            dds.drain()
        await asyncio.sleep(0.3)
        dds.drain()
        found["declared_after"] = (await s.get(gw.base + ENT + "/ent:declared",
                                               headers=a)).status
        found["observed_after"] = (await s.get(gw.base + ENT + "/ent:observed",
                                               headers=a)).status
        stop.set()
        for t in tasks:
            t.cancel()
        found["t_obs"] = t_obs
        found["ws"] = ws
        found["dds"] = list(dds.events)
    dds.close()
    return found


def lapse_of(found: dict) -> tuple[float | None, list, list]:
    """When the observed entity was withdrawn, and how, on socket and bus."""
    ws = [(t, m) for t, m in found["ws"] if m.get("key") == "ent:observed"]
    term = first(ws, lambda e: e[1].get("event") == "sample"
                 and e[1]["data"]["state"] == "RETIRED")
    disp = first(ws, lambda e: e[1].get("event") == "dispose")
    bus = [e for e in found["dds"] if e[3] == "ent:observed"]
    when = (term[0] - found["t_obs"]) if term else None
    return when, [term, disp], bus


async def restart(checks: list, journal: Path) -> None:
    st = fresh_state()
    async with Gateway(st, publish=True, journal=journal, tokens=tokens()) as gw, \
            aiohttp.ClientSession() as s:
        a = {"Authorization": f"Bearer {ALICE}"}
        ents = {d["entity_id"]: d for d in
                await (await s.get(gw.base + ENT, headers=a)).json()}
        claims = {d["id"]: d for d in
                  await (await s.get(gw.base + CLM, headers=a)).json()}
        leases = sorted(k for _t, k in st.leases)
    checks.append((
        "a gateway restarted on the journal serves the final state: the "
        "declared and the refreshed entities, not the lapsed one, the claim "
        "with its asserter, and the refreshed one's lease re-armed",
        sorted(ents) == ["ent:declared", "ent:kept"]
        and claims.get("c1", {}).get("asserter") == "alice"
        and leases == ["ent:kept"],
        f"entities {sorted(ents)}; claims "
        f"{ {k: v.get('asserter') for k, v in claims.items()} }; leases {leases}"))


async def main_async(checks: list) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        found = await journey(checks, Path(tmp) / "j.mcap", bus_.free_domain())

        st_, body = found["no_interval"]
        checks.append((
            "basis: an observed PUT with no refresh interval is refused, and "
            "the refusal names the rule",
            st_ == 400 and body.get("type", "").endswith(":refresh-required")
            and body.get("rule") == "basis" and "OBSERVED" in body["detail"],
            f"{st_} {body.get('type')} rule={body.get('rule')}: "
            f"{body.get('detail', '')[:90]}"))

        checks.append(("the world from HTTP: a declared entity, an observed "
                       "one with an interval, and a claim between them, all "
                       "accepted", found["puts"] == [201, 201, 201],
                       f"{found['puts']}"))
        served = found["served"]
        ws_samples = {m["key"]: m["data"] for _t, m in found["ws"]
                      if m.get("event") == "sample"
                      and m["data"].get("state") != "RETIRED"}
        checks.append(("each reaches a WebSocket subscriber as an ordinary "
                       "sample, equal to what the gateway serves",
                       all(ws_samples.get(k) == v for k, v in served.items()),
                       f"{sorted(ws_samples)}"))
        bus_first = {}
        for _t, lane, kind, key, doc, _r in found["dds"]:
            if kind == "sample" and key not in bus_first:
                bus_first[key] = doc
        checks.append(("and a DDS reader on another participant as the same: "
                       "its samples, as N.1, equal what the gateway serves",
                       all(bus_first.get(k) == v for k, v in served.items()),
                       f"{sorted(bus_first)}; differ "
                       f"{[k for k, v in served.items() if bus_first.get(k) != v]}"))

        claim_doc = served["c1"]
        st_, body = found["impostor"]
        checks.append((
            "the asserter: a claim naming someone other than the token's "
            "principal is refused with its problem document",
            st_ == 403 and body.get("type", "").endswith(":asserter-not-principal")
            and body.get("principal") == "bob",
            f"{st_} {body.get('type')}: {body.get('detail', '')[:90]}"))
        checks.append(("and the accepted claim's asserter is the principal, "
                       "stamped from an empty body",
                       claim_doc.get("asserter") == "alice",
                       f"asserter {claim_doc.get('asserter')!r}"))

        when, (term, disp), bus = lapse_of(found)
        quote = (f"{term[1]['data']['state']} {term[1]['data']['state_reason']!r}"
                 f" / dispose {disp[1].get('reason')!r}") if term and disp else "-"
        print(f"  quote  from the live subscriber: {quote}, "
              f"{when:.2f} s after a {REFRESH:g} s interval" if when else
              "  quote  (no withdrawal seen)")
        checks.append((
            f"the lapse: unrefreshed, the observed entity is withdrawn inside "
            f"the window ({REFRESH:g} s to {REFRESH + SLACK:g} s), terminal "
            f"sample then dispose, both 'refresh lapsed'",
            when is not None and REFRESH <= when <= REFRESH + SLACK
            and disp is not None and disp[0] >= term[0]
            and term[1]["data"]["state_reason"] == "refresh lapsed"
            and disp[1].get("reason") == "refresh lapsed",
            f"{quote}; at {when}"))
        bus_kinds = [(k, d["state"] if d else "", r) for _t, _l, k, _key, d, r in bus]
        checks.append((
            "and on the bus the same: the gateway's writer withdraws it",
            ("sample", "RETIRED", "") in [(k, s, "") for k, s, _r in bus_kinds]
            and ("dispose", "", "refresh lapsed") in bus_kinds,
            f"{bus_kinds}"))
        checks.append(("the declared entity, under the same silence, is still "
                       "asserted", found["declared_after"] == 200
                       and found["observed_after"] == 404,
                       f"declared {found['declared_after']}, observed "
                       f"{found['observed_after']}"))
        kept = [m for _t, m in found["ws"] if m.get("key") == "ent:kept"]
        checks.append((
            f"refresh works: re-PUT every 0.5 s on a 1 s interval kept it "
            f"for {found['kept_s']:.1f} s, never withdrawn",
            found["kept_s"] >= 4.0 and kept
            and not any(m.get("event") == "dispose" for m in kept),
            f"{len(kept)} event(s), dispose {any(m.get('event') == 'dispose' for m in kept)}"))
        await restart(checks, Path(tmp) / "j.mcap")

    # The injection arm: the same journey with the gateway's reaper stopped.
    with tempfile.TemporaryDirectory() as tmp:
        planted = await journey([], Path(tmp) / "j.mcap", bus_.free_domain(),
                                inject_no_reaper=True)
        when, _pair, _bus = lapse_of(planted)
        checks.append((
            "injection: with the reaper stopped the lapse check fails (no "
            "withdrawal, the observed entity still served), so the withdrawal "
            "above is the mechanism's",
            when is None and planted["observed_after"] == 200,
            f"withdrawn at {when}; observed {planted['observed_after']}"))


def run() -> int:
    checks: list = []
    try:
        asyncio.run(main_async(checks))
    except Exception as e:                                # noqa: BLE001
        checks.append(("the gate ran to its end", False,
                       f"{type(e).__name__}: {e}"))
    return report("a world from HTTP", checks,
                  "entities and claims are published, refreshed, lapsed and "
                  "grounded as ruled, over HTTP and on the bus")


if __name__ == "__main__":
    sys.exit(run())
