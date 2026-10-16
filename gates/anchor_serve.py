#!/usr/bin/env python3
"""The gateway serves the anchor recording unmodified, and discovery finds it.

The recording is handed to the existing gateway with no changes to it, which is
the claim: a second recording of different types needs no new serving code.
Checked here: anchors and content announces appear as latched GET resources,
each instance addressable by its `@key`, every response carrying an ETag that
is its content digest, every served instance validating against its published
schema, and a geohash query over the fountain's own cell returning the service.

The geohash is computed from the recording's anchors rather than written down,
so the query under test is the one a client with a GPS fix would actually form.

Usage:  gates/anchor_serve.py
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
# The gateway lives in the distribution under lib/spatialdds; a gate runs
# from a checkout, so the checkout's copy goes on the path rather than an
# installed one.
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402
import jsonschema                                              # noqa: E402
from urllib.parse import quote                                 # noqa: E402
from yarl import URL                                           # noqa: E402

from harness import Gateway, report, state                     # noqa: E402
from spatialdds.server import gateway, webjson                     # noqa: E402

MCAP = ROOT / "samples" / "fountain-anchors.mcap"
INPUT = ROOT / "samples" / "fountain-anchors.input.json"


async def run() -> int:
    doc = json.loads(INPUT.read_text())
    st = state(MCAP)
    gw = await Gateway(st).start()
    checks: list[tuple[str, bool, str]] = []
    schemas = webjson.schemas()

    async with aiohttp.ClientSession() as s:
        anchors = [r for r in st.resources.values()
                   if r.fqn == "spatial.core.GeoAnchor"]
        content = [r for r in st.resources.values()
                   if r.fqn == "spatial.disco.ContentAnnounce"]
        svc = [r for r in st.resources.values()
               if r.fqn == "spatial.disco.Announce"]
        checks.append(("the recording yields one anchor, one content and one "
                       "service-announce resource",
                       len(anchors) == 1 and len(content) == 1
                       and len(svc) == 1,
                       f"{len(anchors)} anchor, {len(content)} content, "
                       f"{len(svc)} announce"))
        checks.append(("all three are latched, so all three are GET resources",
                       all(r.latched for r in anchors + content + svc), ""))
        checks.append(("all three are public, needing no credential: an "
                       "anchor, a content announce and a service announce "
                       "describe places, not people",
                       all(r.access == gateway.PUBLIC
                           for r in anchors + content + svc), ""))

        n_inst = n_valid = 0
        bad_etag, bad_valid, bad_instance = [], [], []
        for res in anchors + content + svc:
            async with s.get(gw.base + res.path) as r:
                code = r.status
                coll_etag = r.headers.get("ETag", "")
                coll = await r.json()
            checks.append((f"{res.type_seg}: the collection is served without "
                           f"auth ({len(coll)} instance(s))",
                           code == 200 and isinstance(coll, list), f"{code}"))
            if not coll_etag.startswith('"sha256:'):
                bad_etag.append(f"{res.path} collection")
            for inst in coll:
                n_inst += 1
                key = str(inst.get(res.key, ""))
                # The key is ONE path segment, so it is percent-encoded.
                # `GeoAnchor.anchor_id` is documented in the IDL as
                # e.g. "anchor/4th-and-main" -- the specification's own example
                # contains the path separator -- and an unencoded key simply
                # 404s. Appendix N.3 states no encoding rule; that is a
                # reported finding, and this is the correct client behaviour
                # in the meantime.
                enc = quote(key, safe="")
                async with s.get(URL(f"{gw.base}{res.path}/{enc}",
                                     encoded=True)) as r2:
                    if r2.status != 200:
                        bad_instance.append(
                            f"{res.path}/{enc} -> {r2.status}")
                        continue
                    one = await r2.json()
                    etag = r2.headers.get("ETag", "")
                if one != inst:
                    bad_instance.append(f"{res.path}/{key} differs from the "
                                        f"collection entry")
                if etag != f'"sha256:{webjson.digest(one)}"':
                    bad_etag.append(f"{res.path}/{key}")
                try:
                    jsonschema.validate(inst, schemas[res.fqn])
                    n_valid += 1
                except jsonschema.ValidationError as e:
                    bad_valid.append(f"{res.fqn}: {e.message[:70]}")

        checks.append((f"every one of the {n_inst} instance(s) is addressable "
                       f"by its percent-encoded @key and matches its "
                       f"collection entry",
                       not bad_instance, f"{bad_instance[:3]}"))
        checks.append(("every ETag is the content digest of what was served",
                       not bad_etag, f"{bad_etag[:3]}"))
        checks.append((f"all {n_valid} served instance(s) validate against "
                       f"their published schema",
                       not bad_valid and n_valid == n_inst,
                       f"{bad_valid[:2]}" or f"{n_valid} of {n_inst}"))

        # Discovery: the cell a client at the fountain would ask about.
        lats = [a["geopose"]["lat_deg"] for a in doc["anchors"]]
        lons = [a["geopose"]["lon_deg"] for a in doc["anchors"]]
        clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
        cell = gateway.geohash_encode(clat, clon, 6)
        async with s.get(f"{gw.base}/.well-known/spatialdds/search"
                         f"?geohash={cell}") as r:
            found = await r.json()
        checks.append((f"a geohash query over the fountain's own cell "
                       f"({cell}) returns the service",
                       len(found.get("results", [])) == 1,
                       f"{len(found.get('results', []))} result(s)"))
        if found.get("results"):
            cov = found["results"][0].get("coverage") or {}
            box = gateway.coverage_bbox(cov)
            inside = (box is not None and box[0] <= clon <= box[2]
                      and box[1] <= clat <= box[3])
            checks.append(("the advertised coverage contains the anchors, "
                           "whichever form it takes",
                           inside, f"coverage {cov} -> {box}"))
            topics = found["results"][0]["service"]["topics"]
            kinds = {t["type"] for t in topics}
            checks.append((f"the manifest lists every served topic with a URL "
                           f"({sorted(kinds)})",
                           kinds == {"geo_anchor", "content_announce",
                                     "announce"}
                           and all(t.get("url") for t in topics), ""))
            checks.append(("both are advertised TRANSIENT_LOCAL",
                           all(t.get("durability") == "TRANSIENT_LOCAL"
                               for t in topics),
                           f"{[t.get('durability') for t in topics]}"))

        # Declared beats derived, and the declared footprint must not
        # under-report what it covers.
        if found.get("results"):
            man = found["results"][0]
            cov = man.get("coverage") or {}
            checks.append(("the manifest serves the recording's declared "
                           "coverage rather than a derived enclosure",
                           man["service"].get("coverage_declared") is True
                           and cov.get("has_circle") is True,
                           f"declared={man['service'].get('coverage_declared')} "
                           f"cov={cov}"))
            checks.append(("the declared coverage is a circle, which is honest "
                           "because a person stated its centre and radius",
                           cov.get("has_circle") and not cov.get("has_bbox"),
                           f"{cov}"))
            declared = gateway.coverage_bbox(cov)
            derived = gateway.scene_bbox(st, st.scenes[0])
            encloses = (declared is not None and derived is not None
                        and declared[0] <= derived[0] and declared[1] <= derived[1]
                        and declared[2] >= derived[2] and declared[3] >= derived[3])
            checks.append(("the declared coverage encloses the enclosure the "
                           "gateway would have derived: a declared footprint "
                           "smaller than the resources it covers is an "
                           "under-report, and that is the one direction "
                           "coverage must never err",
                           encloses,
                           f"declared {declared} does not contain derived "
                           f"{derived}"))

        # A far-away cell must not match: coverage that matches everything is
        # not coverage.
        async with s.get(f"{gw.base}/.well-known/spatialdds/search"
                         f"?geohash=9q8yy") as r:
            far = await r.json()
        checks.append(("a cell in San Francisco returns nothing",
                       len(far.get("results", [])) == 0,
                       f"{len(far.get('results', []))} result(s)"))

        # Two sources of one fact. Since the 1.8 revision the recording's
        # Announce carries `durability` on each TopicMeta entry, and N.7 has
        # always required it on each manifest topic entry -- and the gateway
        # derives the manifest's from what it serves while the announce's
        # comes from the recording. Two places that can disagree about the
        # same thing is a defect class this repository has met before, so they
        # are compared per topic rather than trusted to coincide.
        async with s.get(gw.base + "/.well-known/spatialdds/search") as r:
            man = (await r.json())["results"][0]
        from_manifest = {t["name"]: t["durability"]
                         for t in man["service"]["topics"]}
        ares = next(x for x in st.resources.values()
                    if x.fqn == "spatial.disco.Announce")
        async with s.get(gw.base + ares.path) as r:
            ann = (await r.json())[0]
        from_announce = {t["name"]: t.get("durability") for t in ann["topics"]}
        shared = sorted(set(from_manifest) & set(from_announce))
        disagree = [f"{t}: manifest {from_manifest[t]!r} vs announce "
                    f"{from_announce[t]!r}" for t in shared
                    if from_manifest[t] != from_announce[t]]
        checks.append((f"the manifest's durability and the announce's "
                       f"TopicMeta durability agree on every topic both name "
                       f"({len(shared)} of {len(from_announce)})",
                       bool(shared) and not disagree, "; ".join(disagree)))
        # The announce names its own topics, so a topic it describes that the
        # manifest does not would mean the gateway is serving less than the
        # recording declares.
        missing = sorted(set(from_announce) - set(from_manifest))
        checks.append((f"and the manifest covers every topic the announce "
                       f"declares", not missing, f"absent: {missing}"))

    await gw.stop()
    return report(f"serve and read {MCAP.name}", checks,
                  "the recording is served unmodified and discovery finds it")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
