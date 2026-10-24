#!/usr/bin/env python3
"""Lanes: tokens scoped to scenes, refused across them, and each lane alone.

The deployed sandbox's shape, in process: the fountain seed in one scene
(`ut-austin-south-mall`, James's lane) and an empty scene for an outside group
(`wow-world`), each with its own token, from a token table on disk. Every
expected value is a status this gate states or bytes it captured from the
endpoint *before* the action under test, so nothing is compared against the
gateway's own account of what it did.

What is checked, in the order the brief's acceptance lists it:

* a write across lanes is a 403 problem document naming the scene, in both
  directions, and each lane's own publish, update and withdraw round trip
  works -- manifests included, now that a manifest can be withdrawn;
* the seed is byte-identical through all of it;
* a scene that does not exist is refused to a principal that may not create
  one, and a wildcard grant does not imply the right to create;
* every journal entry carries its principal and scene, and survives a restart
  with them;
* a per-scene reset empties one lane and leaves the other byte-identical,
  served bytes and journal bytes both;
* the table holds no plaintext token, and a reload revokes without a restart.

    gates/tenancy.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

from harness import FOUNTAIN, MANIFESTS, Gateway, report, state  # noqa: E402
from publish_common import delete, get, put, resolve_path      # noqa: E402

from spatialdds.server import publish, tenancy                 # noqa: E402

AUTH = "demo.spatialdds.org"
LANE1, LANE2 = "ut-austin-south-mall", "wow-world"
DUCK = json.loads((ROOT / "samples" / "duck.input.json").read_text())
SEED_URIS = [f"spatialdds://{AUTH}/{LANE1}/content/fountain-marker",
             f"spatialdds://{AUTH}/{LANE1}/content/fountain-label"]
LANE1_PATHS = [f"/{LANE1}/content/content_announce/v1",
               f"/{LANE1}/discovery/announce/v1",
               f"/{LANE1}/frames/geo_anchor/v1",
               *[resolve_path(u) for u in SEED_URIS]]


def duck_in(scene: str, key_name: str = "duck") -> tuple[dict, str, dict]:
    """The committed duck, moved into `scene`: (manifest, announce path, record)."""
    m = copy.deepcopy(DUCK["manifest"])
    a = copy.deepcopy(DUCK["announce"])
    uri = f"spatialdds://{AUTH}/{scene}/content/{key_name}"
    m["id"] = uri
    m["content"]["content_id"] = f"content/{key_name}"
    rec = a["record"]
    rec["content_id"] = f"content/{key_name}"
    rec["manifest_uri"] = uri
    path = f"/{scene}/content/content_announce/v1/" + quote(
        f"content/{key_name}", safe="")
    return m, path, rec


async def snapshot(s, base: str, paths: list[str]) -> dict:
    out = {}
    for p in paths:
        code, _h, body = await get(s, base, p)
        out[p] = (code, body)
    return out


def problem(body: bytes) -> dict:
    try:
        return json.loads(body)
    except ValueError:
        return {}


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    toks = {n: tenancy.issue() for n in ("james", "wow", "ops", "maker")}
    with tempfile.TemporaryDirectory() as tmp:
        tfile = Path(tmp) / "tokens.json"
        tenancy.TokenTable([
            (tenancy.digest(toks["james"]), tenancy.Principal(
                "james", "openarcloud.org", frozenset({LANE1}))),
            (tenancy.digest(toks["wow"]), tenancy.Principal(
                "wow", "webofworlds.example", frozenset({LANE2}))),
            (tenancy.digest(toks["ops"]), tenancy.Principal(
                "ops", "openarcloud.org", frozenset({tenancy.ALL}))),
            (tenancy.digest(toks["maker"]), tenancy.Principal(
                "maker", "openarcloud.org", frozenset({tenancy.ALL}),
                may_create_scenes=True)),
        ], tfile).save()
        text = tfile.read_text()
        checks.append(("the token table holds digests, never a token",
                       not any(t in text for t in toks.values())
                       and text.count("token_sha256") == 4, ""))
        table = tenancy.TokenTable.load(tfile)
        jp = Path(tmp) / "registry.mcap"

        async with aiohttp.ClientSession() as s:
            gw = await Gateway(state(FOUNTAIN, MANIFESTS),
                               advertise=f"https://{AUTH}", publish=True,
                               journal=jp, tokens=table).start()
            b = gw.base
            seed = await snapshot(s, b, LANE1_PATHS)
            checks.append(("the seed is served before anything is written",
                           all(c == 200 for c, _ in seed.values()),
                           f"{[c for c, _ in seed.values()]}"))

            m1, p1, r1 = duck_in(LANE1)
            m2, p2, r2 = duck_in(LANE2)

            # -- 401: no credential, or one nobody issued --
            code, h, body = await put(s, b, p1, r1, token=None)
            checks.append(("a write with no token is 401 with WWW-Authenticate",
                           code == 401 and "WWW-Authenticate" in h,
                           f"{code} {h.get('WWW-Authenticate')}"))
            code, _h, _b = await put(s, b, p1, r1, token=tenancy.issue())
            checks.append(("a write with a token nobody issued is 401",
                           code == 401, f"{code}"))

            # -- 403 across lanes, both directions, quoted --
            code, _h, body = await put(s, b, p2, r2, token=toks["james"])
            d = problem(body)
            print(f"  james -> {LANE2}: {code} {body.decode()}")
            checks.append((f"james's token writing {LANE2} is a 403 "
                           f"not-your-lane naming the scene",
                           code == 403 and d.get("type", "").endswith(
                               "not-your-lane") and d.get("scene") == LANE2,
                           f"{code} {d}"))
            code, _h, body = await put(s, b, p1, r1, token=toks["wow"])
            d = problem(body)
            print(f"  wow -> {LANE1}: {code} {body.decode()}")
            checks.append((f"wow's token writing {LANE1} is a 403 "
                           f"not-your-lane naming the scene",
                           code == 403 and d.get("type", "").endswith(
                               "not-your-lane") and d.get("scene") == LANE1,
                           f"{code} {d}"))
            code, _h, _b = await put(s, b, "/spatialdds/resolve", m1,
                                     token=toks["wow"])
            checks.append((f"wow's token publishing a manifest under {LANE1}'s "
                           f"URI is 403", code == 403, f"{code}"))
            code, _h, _b = await delete(s, b, resolve_path(SEED_URIS[0]),
                                        token=toks["wow"])
            checks.append((f"wow's token withdrawing {LANE1}'s seed manifest "
                           f"is 403", code == 403, f"{code}"))
            code, _h, _b = await delete(s, b, p1.replace("duck", "x"),
                                        token=toks["wow"])
            checks.append((f"wow's token disposing in {LANE1} is 403 before "
                           f"anything is looked up", code == 403, f"{code}"))

            # -- each lane's own round trip --
            for who, m, p, rec in (("james", m1, p1, r1),
                                   ("wow", m2, p2, r2)):
                t = toks[who]
                c1, _h, _b = await put(s, b, "/spatialdds/resolve", m, token=t)
                c2, _h, _b = await put(s, b, p, rec, token=t)
                rec2 = {**rec, "title": rec["title"] + " (updated)"}
                c3, _h, _b = await put(s, b, p, rec2, token=t)
                _c, _h, got = await get(s, b, p)
                c4, _h, _b = await delete(s, b, p + "?reason=round%20trip",
                                          token=t)
                c5, _h, _b = await get(s, b, p)
                c6, _h, _b = await delete(
                    s, b, resolve_path(m["id"]) + "&reason=round%20trip",
                    token=t)
                c7, _h, _b = await get(s, b, resolve_path(m["id"]))
                ok = ((c1, c2, c3, c4, c5, c6, c7)
                      == (201, 201, 200, 204, 404, 204, 404)
                      and json.loads(got)["title"].endswith("(updated)"))
                checks.append((f"{who}'s own lane: manifest PUT, announce PUT, "
                               f"update, withdraw, manifest withdraw, both "
                               f"gone", ok, f"{(c1, c2, c3, c4, c5, c6, c7)}"))

            # -- scenes that do not exist --
            m3, p3, r3 = duck_in("nowhere")
            code, _h, body = await put(s, b, p3, r3, token=toks["wow"])
            checks.append(("wow's token writing an unknown scene is 403 "
                           "not-your-lane (it is not granted)",
                           code == 403 and problem(body).get("type", "")
                           .endswith("not-your-lane"), f"{code}"))
            code, _h, body = await put(s, b, p3, r3, token=toks["ops"])
            print(f"  ops (*, no create) -> nowhere: {code} {body.decode()}")
            checks.append(("a wildcard grant without may_create_scenes may not "
                           "mint a scene: 403 scene-creation-not-granted",
                           code == 403 and problem(body).get("type", "")
                           .endswith("scene-creation-not-granted"), f"{code}"))
            code, _h, _b = await put(s, b, p2, r2, token=toks["ops"])
            c2, _h, _b = await delete(s, b, p2, token=toks["ops"])
            checks.append((f"the same wildcard grant may write {LANE2}, which "
                           f"exists because a grant names it, though empty",
                           (code, c2) == (201, 204), f"{(code, c2)}"))
            code, _h, _b = await get(s, b, f"/{LANE2}/content/content_announce"
                                           f"/v1")
            checks.append((f"reads need no token: {LANE2}'s collection is "
                           f"served openly", code == 200, f"{code}"))

            # -- leave one record in each lane, attributed --
            for who, m, p, rec in (("james", m1, p1, r1), ("wow", m2, p2, r2)):
                await put(s, b, "/spatialdds/resolve", m, token=toks[who])
                await put(s, b, p, rec, token=toks[who])
            lane2_paths = [f"/{LANE2}/content/content_announce/v1",
                           resolve_path(m2["id"])]
            both = LANE1_PATHS + [p1, resolve_path(m1["id"])] + lane2_paths
            before = await snapshot(s, b, both)
            seed_now = await snapshot(s, b, LANE1_PATHS[1:3])
            checks.append(("the seed's anchors and announce are byte-identical "
                           "after every write above",
                           seed_now == {k: seed[k] for k in LANE1_PATHS[1:3]},
                           ""))
            await gw.stop()

            items, _idl = publish.read_entries(jp)
            who_wrote = {(e["scene"], e["principal"]) for _q, e in items}
            checks.append(("every journal entry carries its principal and "
                           "scene, and only the principals that wrote",
                           who_wrote == {(LANE1, "james"), (LANE2, "wow"),
                                         (LANE2, "ops")}
                           and all(e["principal"] for _q, e in items),
                           f"{sorted(who_wrote)}"))

            # -- restart: same state, principals intact --
            gw = await Gateway(state(FOUNTAIN, MANIFESTS),
                               advertise=f"https://{AUTH}", publish=True,
                               journal=jp, tokens=table).start()
            after = await snapshot(s, gw.base, both)
            checks.append(("a restart serves both lanes byte-identically",
                           after == before,
                           f"{[p for p in both if after[p] != before[p]]}"))
            kept = {(e.get("scene"), e.get("principal"))
                    for e in gw.journal.entries}
            checks.append(("the replayed journal keeps each entry's principal",
                           kept == who_wrote, f"{sorted(kept)}"))
            await gw.stop()

            # -- per-scene reset: lane 2 only --
            lane1_entries = [(q, e) for q, e in items if e["scene"] == LANE1]
            removed, left = publish.reset_scene(jp, LANE2)
            items2, _idl = publish.read_entries(jp)
            checks.append((f"reset of {LANE2} removes its entries and keeps "
                           f"{LANE1}'s byte for byte (seq, record, principal)",
                           items2 == lane1_entries and removed > 0,
                           f"removed {removed}, kept {left}"))
            gw = await Gateway(state(FOUNTAIN, MANIFESTS),
                               advertise=f"https://{AUTH}", publish=True,
                               journal=jp, tokens=table).start()
            lane1_now = await snapshot(s, gw.base, LANE1_PATHS
                                       + [p1, resolve_path(m1["id"])])
            checks.append((f"after the reset {LANE1} serves exactly what it "
                           f"served before",
                           lane1_now == {k: before[k] for k in lane1_now}, ""))
            gone = await snapshot(s, gw.base, lane2_paths)
            checks.append((f"after the reset {LANE2} is empty again",
                           all(c == 404 for c, _ in gone.values()),
                           f"{[c for c, _ in gone.values()]}"))

            # -- reload: revoke without a restart, and a bad file is refused --
            tenancy.TokenTable(
                [(h, p) for h, p in table.rows if p.name != "wow"],
                tfile).save()
            table.reload()
            code, _h, _b = await put(s, gw.base, p2, r2, token=toks["wow"])
            checks.append(("a reload after revoking wow makes wow's token 401, "
                           "with no restart", code == 401, f"{code}"))
            tfile.write_text("{not json")
            try:
                table.reload()
                refused = False
            except ValueError:
                refused = True
            code, _h, _b = await put(s, gw.base, p1, r1, token=toks["james"])
            checks.append(("a table that does not load is refused and the old "
                           "one keeps serving", refused and code == 200,
                           f"refused={refused} james={code}"))
            await gw.stop()

        # -- may_create_scenes, on its own endpoint so it changes nothing above
        async with aiohttp.ClientSession() as s:
            gw = await Gateway(state(FOUNTAIN, MANIFESTS),
                               advertise=f"https://{AUTH}", publish=True,
                               tokens=tenancy.TokenTable([
                                   (tenancy.digest(toks["maker"]),
                                    tenancy.Principal(
                                        "maker", "openarcloud.org",
                                        frozenset({tenancy.ALL}), True))]
                               )).start()
            m3, p3, r3 = duck_in("brand-new")
            c1, _h, _b = await put(s, gw.base, "/spatialdds/resolve", m3,
                                   token=toks["maker"])
            c2, _h, _b = await put(s, gw.base, p3, r3, token=toks["maker"])
            checks.append(("a principal with may_create_scenes brings a new "
                           "scene into existence", (c1, c2) == (201, 201),
                           f"{(c1, c2)}"))
            await gw.stop()

    return report("tenancy", checks,
                  "writes are per lane, refused across lanes, and each lane "
                  "persists and resets alone")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
