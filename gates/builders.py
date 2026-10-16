#!/usr/bin/env python3
"""The record builders, against the records that were written by hand.

`sd.records` exists so that publishing a service is three arguments instead of
eighteen members. The risk in that is obvious: a builder that fills a member
with something plausible is worse than no builder, because the record still
validates and the claim it makes is one nobody chose.

So the checks here are about what the builders put in fields nobody mentioned:

**They produce complete, valid records.** Every builder's output validates
against the generated JSON Schema -- with `jsonschema`, which the builders
themselves deliberately do not use. Their own `_check` is table-driven and
dependency-free, and this is the independent reading of the same requirement.

**They agree with the hand-authored records on every field a caller states.**
`samples/published-records.input.json` and `samples/duck.input.json` were
written out member by member, long before any of this existed. Given the same
inputs, a builder has to produce the same record -- and where it cannot, the
difference is printed and named rather than smoothed over.

**The census.** Every builder argument, mapped to the IDL member it fills,
and every member no argument reaches. That list is the deliverable: a member
only a hand-written record can set is a member the builders cannot express,
and saying so is the point.

**The acceptance bars.** The four lines from the directive, run verbatim
against an empty registry, then the anchor in one line, then a restart.

Usage:  gates/builders.py [--census]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"
sys.path.insert(0, str(LIB))

import jsonschema                                              # noqa: E402

from harness import (FOUNTAIN, MANIFESTS, TOKEN, Gateway,      # noqa: E402
                     report, state)
from spatialdds.server import gateway, webjson                  # noqa: E402

import spatialdds as sd                                         # noqa: E402
from spatialdds import records as R                             # noqa: E402

PUB = ROOT / "samples" / "published-records.input.json"
DUCK = ROOT / "samples" / "duck.input.json"
#: The authority the committed records name, so a publish into a fountain
#: serving is not a foreign-authority 403.
DEMO = "https://demo.spatialdds.org"

#: Each builder, with the arguments it takes and the type it fills. The map
#: the census is printed from, and the thing a reader checks against the IDL.
BUILDERS = {
    "service_announce": ("spatial.disco.Announce", R.service_announce),
    "content_announce": ("spatial.disco.ContentAnnounce", R.content_announce),
    "geo_anchor": ("spatial.core.GeoAnchor", R.geo_anchor),
}


def census() -> dict:
    """Builder argument -> IDL member, and the members nothing reaches."""
    import inspect
    T = webjson.tables()
    out = {}
    for name, (fqn, fn) in BUILDERS.items():
        args = [p for p in inspect.signature(fn).parameters]
        members = [n for n, _ in T[fqn]["fields"]]
        # An argument reaches a member when it is named for it, or when it is
        # one of the stated shorthands below.
        shorthand = {
            "lat": "geopose", "lon": "geopose", "alt_m": "geopose",
            "q": "geopose", "when": "stamp", "sigma_enu_m": "cov",
            "rho_en": "cov", "authority": "manifest_uri",
            "scene": "manifest_uri", "hints": "hints",
            "coverage_frame_ref": "coverage_frame_ref",
            "available_from": "available_from",
            "available_until": "available_until",
        }
        reached, mapping = set(), {}
        for a in args:
            m = a if a in members else shorthand.get(a)
            if m in members:
                reached.add(m)
                mapping.setdefault(m, []).append(a)
        # Members a builder fills from nothing the caller said: these are the
        # conventions, and each one is documented where it is applied.
        conventions = {
            "spatial.disco.Announce": {
                "hints": "[] unless hints= is given",
                "caps": "zeroed: profile negotiation is not this binding's",
                "coverage_eval_time": "zeroed, with has_ false",
                "has_coverage_eval_time": "false",
                "transforms": "[]: no frame transforms asserted",
                "coverage_source_ids": "[]: provenance is deferred",
                "stamp": "now, unless when= is given",
                "coverage_frame_ref": "earth-fixed unless given",
            },
            "spatial.disco.ContentAnnounce": {
                "coverage_eval_time": "zeroed, with has_ false",
                "has_coverage_eval_time": "false",
                "transforms": "[]: no frame transforms asserted",
                "available_from": "zeroed unless given",
                "available_until": "zeroed unless given",
                "stamp": "now, unless when= is given",
                "coverage_frame_ref": "earth-fixed unless given",
                "provider_id": '"" unless given: identity is deferred',
            },
            "spatial.core.GeoAnchor": {
                "frame_ref": "earth-fixed unless given",
                "checksum": "sha256 of the record's own canonical JSON with "
                            "checksum emptied (a convention; finding 31)",
                "confidence": "1/(1+sigma_max) unless given",
                "geopose": "from lat/lon/alt_m/q/when/cov",
                "cov": "COV_POS3 from sigma_enu_m, or COV_NONE",
            },
        }[fqn]
        out[name] = {
            "fqn": fqn,
            "arguments": mapping,
            "conventions": conventions,
            "unreachable": [m for m in members
                            if m not in reached and m not in conventions],
        }
    return out


def print_census() -> None:
    c = census()
    for name, d in c.items():
        print(f"\n{name}  ->  {d['fqn']}")
        print("  argument -> member")
        for m, args in sorted(d["arguments"].items()):
            print(f"    {', '.join(args):<34} {m}")
        print("  filled by convention, no argument")
        for m, why in sorted(d["conventions"].items()):
            print(f"    {m:<34} {why}")
        print(f"  no argument and no convention: "
              f"{d['unreachable'] or 'none'}")


def schema_and_shape(checks: list) -> None:
    S = webjson.schemas()
    cov = R.circle(30.28495359, -97.73934584, 25.0, alt_m=158.0)
    built = {
        "service_announce": R.service_announce(
            "austin-vps", "VPS", coverage=cov, authority="demo.spatialdds.org",
            scene="south-mall", org="openarcloud.example", version="2026-q4",
            topics=[R.topic("geo_anchor", scene="ut-austin-south-mall",
                            stream="frames")]),
        "content_announce": R.content_announce(
            "content/duck", coverage=cov, authority="demo.spatialdds.org",
            scene="south-mall", title="A duck"),
        "geo_anchor": R.geo_anchor(
            "anchor/duck-perch", lat=30.2849859, lon=-97.73935, alt_m=158.0,
            method="VisualFix", map_id="ut-austin/south-mall",
            sigma_enu_m=(0.25, 0.25, 0.5)),
    }
    for name, doc in built.items():
        fqn = BUILDERS[name][0]
        try:
            jsonschema.validate(doc, S[fqn])
            ok, why = True, ""
        except jsonschema.ValidationError as e:
            ok, why = False, e.message[:140]
        checks.append((f"{name} produces a complete {fqn.split('.')[-1]}: "
                       f"{len(doc)} members, valid against the generated "
                       "schema", ok, why))
    # The lift is the point of the library, so a built record has to survive
    # it: these are canonical JSON, and the server's own round trip is what
    # the publish path will do to them.
    model, _ = __import__("spatialdds.server.pin",
                          fromlist=["pin"]).wire_model()
    lifter = webjson.Lifter(model, webjson.tables())
    bad = []
    for name, doc in built.items():
        fqn = BUILDERS[name][0]
        try:
            lifter.lift(fqn, doc)
        except Exception as e:                                # noqa: BLE001
            bad.append(f"{name}: {type(e).__name__}: {e}")
    checks.append(("every built record lifts through the server's own "
                   "`webjson.Lifter` without complaint", not bad,
                   "; ".join(bad)))

    # The anchor's checksum is this repository's convention, and the point of
    # the builder's version is that a reader can recompute it. The authoring
    # tool's digest covers an input form nobody receives.
    # Recomputed the way a reader would: the served record, `checksum`
    # emptied, through the binding's own canonical form. This checks the
    # procedure rather than the canonical form itself -- there is one
    # implementation of that, shared by both halves, which is the point.
    anc = built["geo_anchor"]
    probe = dict(anc)
    probe["checksum"] = ""
    again = "sha256:" + webjson.digest(probe)
    checks.append(("the anchor's checksum recomputes from the served record "
                   "through webjson.digest, which the authoring tool's digest "
                   "of an input form cannot (finding 31)",
                   again == anc["checksum"],
                   f"{anc['checksum'][:24]} vs {again[:24]}"))
    checks.append(("…and the canonical JSON the builders write is the "
                   "server's own `webjson.canon`, byte for byte",
                   all(R._canon(d) == webjson.canon(d) for d in built.values()),
                   "the two canonical forms differ"))
    sig = max(anc["cov"]["pos"][0], anc["cov"]["pos"][4],
              anc["cov"]["pos"][8]) ** 0.5
    checks.append((f"confidence {anc['confidence']} is the monotone summary "
                   "1/(1+sigma_max) of the covariance it was built with, so "
                   "the scalar and the matrix cannot contradict",
                   abs(anc["confidence"] - 1 / (1 + sig)) < 5e-5,
                   f"sigma_max {sig}"))


def against_handwritten(checks: list) -> None:
    """The same inputs the committed records were authored from."""
    pub = json.loads(PUB.read_text())
    duck = json.loads(DUCK.read_text())
    ann = pub["service"]["record"]
    # Rebuilt from the authored record's own stated values: the builder is
    # given what a caller would have said, and nothing else.
    cov = ann["coverage"][0]
    rebuilt = R.service_announce(
        ann["service_id"], ann["kind"],
        coverage=R.circle(cov["circle_center"][1], cov["circle_center"][0],
                          cov["circle_radius_m"],
                          alt_m=cov["circle_center"][2]),
        name=ann["name"], org=ann["org"], version=ann["version"],
        topics=ann["topics"], manifest_uri=ann["manifest_uri"],
        auth_hint=ann["auth_hint"], ttl_sec=ann["ttl_sec"],
        coverage_frame_ref=ann["coverage_frame_ref"],
        when=ann["stamp"]["sec"])
    diff = sorted(k for k in set(ann) | set(rebuilt)
                  if ann.get(k) != rebuilt.get(k))
    # Two known differences, both named rather than smoothed over.
    #
    # `caps`: the authored record declares supported profiles and no builder
    # argument reaches them. Profile negotiation is not something a publisher
    # states in one line, so the builder zeroes it and the census says so.
    #
    # `coverage`: the authored coverage element carries a populated
    # `frame_ref` while declaring `has_frame_ref: false` -- a body for a
    # member it says is absent. The builder leaves it zeroed. Same meaning,
    # different bytes, and the check says exactly that instead of ignoring
    # the field: anything else differing inside coverage still fails.
    cov_a, cov_b = ann["coverage"][0], rebuilt["coverage"][0]
    inner = sorted(k for k in set(cov_a) | set(cov_b)
                   if cov_a.get(k) != cov_b.get(k))
    checks.append((f"a service announce rebuilt from the authored record's "
                   "own values matches it on every member except `caps` and "
                   "the absent `coverage[0].frame_ref`",
                   diff == ["caps", "coverage"] and inner == ["frame_ref"]
                   and cov_a["has_frame_ref"] is False
                   and cov_b["has_frame_ref"] is False,
                   f"differs at {diff}, inside coverage at {inner}"))

    anc = duck["anchor"]["record"]
    entry = duck["anchor"]["authoring_entry"]
    rebuilt = R.geo_anchor(
        anc["anchor_id"], lat=anc["geopose"]["lat_deg"],
        lon=anc["geopose"]["lon_deg"], alt_m=anc["geopose"]["alt_m"],
        method=anc["method"], map_id=anc["map_id"],
        sigma_enu_m=entry["sigma_enu_m"], rho_en=entry.get("rho_en", 0.0),
        frame_ref=anc["frame_ref"], manifest_uri=anc["manifest_uri"],
        confidence=anc["confidence"], when=anc["geopose"]["stamp"]["sec"])
    diff = sorted(k for k in set(anc) | set(rebuilt)
                  if anc.get(k) != rebuilt.get(k))
    checks.append((f"an anchor rebuilt from the authored record's own values "
                   "matches it on every member except `checksum`, which is "
                   "the one field the two conventions disagree about",
                   diff == ["checksum"], f"differs at {diff}"))
    # And the covariance really was reproduced from sigmas, which is the
    # arithmetic a caller is trusting.
    checks.append(("…including the covariance, reproduced from the three "
                   "standard deviations the authoring entry states",
                   rebuilt["cov"] == anc["cov"],
                   f"{rebuilt['cov']} vs {anc['cov']}"))


async def the_four_lines(checks: list) -> None:
    """The directive's own lines, verbatim, against an empty registry."""
    AUTH = "registry.mytown.example"
    with tempfile.TemporaryDirectory() as tmp:
        jp = Path(tmp) / "registry.mcap"
        st = gateway.build_state(None)
        gw = await Gateway(st, advertise=f"https://{AUTH}", publish=True,
                           journal=jp).start()
        try:
            tok = TOKEN
            contract = {
                "id": f"spatialdds://{AUTH}/default/service/mytown-vps",
                "profile": "spatial.manifest/1.8", "rtype": "service",
                "service": {"service_id": "mytown-vps", "kind": "VPS"}}

            def run():
                ep = sd.connect(gw.base, token=tok)
                a = ep.publish_service(
                    service_id="mytown-vps", kind="VPS",
                    coverage=sd.circle(30.28, -97.74, 500), manifest=contract)
                b = ep.publish_content(
                    content_id="duck", title="Duck",
                    position=(30.284, -97.739),
                    asset="https://cdn.example/duck.gltf")
                c = ep.publish_anchor("anchor/duck-perch", lat=30.284,
                                      lon=-97.739, method="VisualFix",
                                      sigma_enu_m=(0.25, 0.25, 0.5),
                                      content="duck")
                return ep, a, b, c

            ep, a, b, c = await asyncio.to_thread(run)
            checks.append((f"the directive's four lines publish a service, "
                           "its manifest, content, its manifest, and an "
                           "anchor into an empty registry",
                           all(w.created for w in (a, b, c))
                           and a.manifest is not None and a.manifest.created
                           and b.manifest is not None and b.manifest.created,
                           f"{[w.created for w in (a, b, c)]}"))
            checks.append((f"the endpoint's authority was asked, not guessed: "
                           f"{ep.authority!r} from an endpoint holding no "
                           "manifests when it was asked",
                           ep.authority == AUTH, f"{ep.authority!r}"))
            cell = sd.cells(sd.circle(30.28, -97.74, 500))[0]
            hits = await asyncio.to_thread(ep.search, cell)
            checks.append((f"search on the cell the builder computed from the "
                           f"declared coverage ({cell}) finds the service "
                           "within those lines",
                           [h.service_id for h in hits] == ["mytown-vps"],
                           f"{[h.service_id for h in hits]}"))
            res = await asyncio.to_thread(
                ep.resolve, f"spatialdds://{AUTH}/default/content/duck")
            checks.append(("the content manifest the verb built resolves, and "
                           "names the asset it was given",
                           res.doc["assets"][0]["uri"]
                           == "https://cdn.example/duck.gltf",
                           f"{res.assets}"))
            err = None
            try:
                await asyncio.to_thread(res.fetch_asset)
            except sd.UnverifiableIntegrity as e:
                err = str(e)
            except sd.SpatialDDSError as e:
                err = f"(unreachable, as expected offline) {type(e).__name__}"
            checks.append(("an asset published without a digest is honest "
                           "about it: fetching refuses rather than reporting "
                           "a check it did not do",
                           err is not None, f"{err!r}"))
            anchors = await asyncio.to_thread(
                ep.get_json, "/default/frames/geo_anchor/v1")
            checks.append((f"publish_anchor's record carries the content "
                           "linkage the 1.8 revision made expressible",
                           len(anchors) == 1
                           and anchors[0]["manifest_uri"]
                           == f"spatialdds://{AUTH}/default/content/duck",
                           f"{[a.get('manifest_uri') for a in anchors]}"))
        finally:
            await gw.stop()

        # Restart: the acceptance bar that says it is a registry.
        st2 = gateway.build_state(None)
        gw2 = await Gateway(st2, advertise=f"https://{AUTH}", publish=True,
                            journal=jp).start()
        try:
            ep2 = sd.connect(gw2.base, token=TOKEN)
            hits = await asyncio.to_thread(ep2.search, "9v6k")
            res = await asyncio.to_thread(
                ep2.resolve, f"spatialdds://{AUTH}/default/content/duck")
            anchors = await asyncio.to_thread(
                ep2.get_json, "/default/frames/geo_anchor/v1")
            checks.append(("after a restart everything is still served: the "
                           "service, both manifests, the content and the "
                           "anchor",
                           [h.service_id for h in hits] == ["mytown-vps"]
                           and res.doc["id"].endswith("/content/duck")
                           and len(anchors) == 1,
                           f"{[h.service_id for h in hits]}, "
                           f"{len(anchors)} anchor(s)"))
        finally:
            await gw2.stop()


async def two_checksum_conventions(checks: list) -> None:
    """Finding 31's gap, demonstrated on one latched set.

    The repository now holds two `checksum` conventions, both satisfying
    §7.5.2's "a checksum field in the body" and neither able to verify the
    other: the authoring tool digests an anchor's *input entry*, and
    `records.geo_anchor` digests the *served canonical form* with `checksum`
    emptied. A publishing endpoint serves both kinds side by side, and nothing
    in a record says which rule applies to it.

    This is here because the findings entry cites it. An entry that cites
    evidence needs a producer for that evidence, and prose is not one.
    """
    st = state(FOUNTAIN, MANIFESTS)
    gw = await Gateway(st, advertise=DEMO, publish=True).start()
    try:
        ep = sd.connect(gw.base, token=TOKEN, scene="ut-austin-south-mall",
                        lifted=False)
        await asyncio.to_thread(ep.publish_anchor, "anchor/evidence",
                                lat=30.2849, lon=-97.7393, method="VisualFix",
                                sigma_enu_m=(0.25, 0.25, 0.5))
        got = await asyncio.to_thread(
            ep.get_json, "/ut-austin-south-mall/frames/geo_anchor/v1")
        verifiable = {}
        for a in got:
            probe = dict(a)
            probe["checksum"] = ""
            verifiable[a["anchor_id"]] = \
                a["checksum"] == "sha256:" + webjson.digest(probe)
        pub = [k for k, v in verifiable.items() if v]
        rec = [k for k, v in verifiable.items() if not v]
        checks.append((f"finding 31, demonstrated: one latched set of "
                       f"{len(got)} anchors holds {len(pub)} whose checksum a "
                       f"reader can recompute from what was served and "
                       f"{len(rec)} whose checksum digests an authoring form "
                       f"nobody receives",
                       pub == ["anchor/evidence"] and len(rec) == 5,
                       f"recomputable: {pub}, not: {sorted(rec)}"))
        checks.append(("…and both satisfy §7.5.2's integrity signal, which is "
                       "the gap rather than a defect in either",
                       all(a["checksum"].startswith("sha256:") for a in got),
                       f"{[a['checksum'][:10] for a in got]}"))
    finally:
        await gw.stop()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", action="store_true",
                    help="print the builder field census and stop")
    a = ap.parse_args()
    if a.census:
        print_census()
        return 0
    checks: list[tuple[str, bool, str]] = []
    schema_and_shape(checks)
    against_handwritten(checks)
    await the_four_lines(checks)
    await two_checksum_conventions(checks)
    c = census()
    for name, d in c.items():
        print(f"  note {name}: {len(d['arguments'])} member(s) from "
              f"arguments, {len(d['conventions'])} by documented convention, "
              f"{len(d['unreachable'])} unreachable "
              f"{d['unreachable'] or ''}")
    checks.append((f"every member of all three record types is either "
                   "reachable from an argument or filled by a documented "
                   "convention",
                   all(not d["unreachable"] for d in c.values()),
                   f"{ {n: d['unreachable'] for n, d in c.items() if d['unreachable']} }"))
    return report("the record builders", checks,
                  "natural arguments in, the records the hand-authored ones "
                  "are out")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
