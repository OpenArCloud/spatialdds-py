#!/usr/bin/env python3
"""One anchor, hand-computed from the authoring input, field by field.

The authoring tool and the recording are the code under test, so neither may
supply an expected value. Everything checked here comes from
`samples/fountain-anchors.input.json`, which is authored data, or from a rule
the specification states.

The anchor checked in full is named below rather than chosen at random, so a
failure names the same instance every time and the comparison is reproducible.
Then the whole set gets the structural checks that only make sense across it:
that every anchor's `confidence` is the monotone summary of its covariance that
`GeoAnchor` requires, and that no two anchors claim the same position.

Usage:  gates/golden_anchor.py
"""
from __future__ import annotations

import json
import math
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

from harness import report                                     # noqa: E402
from spatialdds.server import gateway                              # noqa: E402

MCAP = ROOT / "samples" / "fountain-anchors.mcap"
INPUT = ROOT / "samples" / "fountain-anchors.input.json"
GOLDEN = "anchor/fountain-south-rim"   # the one with directional covariance


def main() -> int:
    doc = json.loads(INPUT.read_text())
    st = gateway.build_state(MCAP)
    topic = next(r.topic for r in st.resources.values()
                 if r.fqn == "spatial.core.GeoAnchor")
    served = st.latched[topic]
    by_id = {a["anchor_id"]: a for a in doc["anchors"]}

    checks: list[tuple[str, bool, str]] = []
    checks.append((f"the recording carries the {len(doc['anchors'])} anchors "
                   f"the input declares",
                   len(served) == len(doc["anchors"]),
                   f"input {len(doc['anchors'])}, recording {len(served)}"))
    checks.append((f"every input anchor_id is a served instance key",
                   sorted(served) == sorted(by_id),
                   f"only in one: {sorted(set(served) ^ set(by_id))[:3]}"))

    want = by_id[GOLDEN]
    got = served.get(GOLDEN)
    if got is None:
        checks.append((f"the golden anchor {GOLDEN} is served", False, ""))
        return report(f"golden anchor {MCAP.name}", checks, "field-by-field")
    d = got.data

    # --- field by field, every expected value from the input ---------------
    checks.append((f"anchor_id == {want['anchor_id']!r}",
                   d["anchor_id"] == want["anchor_id"], f"{d['anchor_id']!r}"))
    checks.append((f"map_id == {want['map_id']!r}",
                   d["map_id"] == want["map_id"], f"{d['map_id']!r}"))
    checks.append((f"method == {want['method']!r}",
                   d["method"] == want["method"], f"{d['method']!r}"))
    checks.append((f"confidence == {want['confidence']}",
                   d["confidence"] == want["confidence"],
                   f"{d['confidence']}"))
    g = d["geopose"]
    for k in ("lat_deg", "lon_deg", "alt_m"):
        checks.append((f"geopose.{k} == {want['geopose'][k]}",
                       g[k] == want["geopose"][k], f"{g[k]}"))
    checks.append((f"geopose.q == {want['geopose']['q']}",
                   list(g["q"]) == list(want["geopose"]["q"]), f"{g['q']}"))

    # Covariance twice: GeoAnchor.cov and the GeoPose.cov beside it, both
    # COV_POS3 carrying the input's row-major matrix.
    for where, node in (("cov", d["cov"]), ("geopose.cov", g["cov"])):
        checks.append((f"{where} is tagged COV_POS3",
                       node.get("type") == "COV_POS3", f"{node.get('type')}"))
        checks.append((f"{where}.pos == the input's row-major 3x3",
                       list(node.get("pos", [])) ==
                       want["cov_pos3_row_major"],
                       f"{node.get('pos')}"))

    # The frame reference comes from the input's single frame_ref.
    fr = d["frame_ref"]
    # The 1.8 revision's content reference, both legs. Expected values come
    # from the input's `content` key and from the authority it states, never
    # from the served record.
    auth = doc["authority"]
    for aid, a in sorted(by_id.items()):
        if aid not in served:
            continue
        got = served[aid].data.get("manifest_uri")
        want_uri = (f"spatialdds://{auth}/south-mall/content/"
                    f"{a['content'].split('/')[-1]}") if a.get("content") else ""
        leg = "names content" if a.get("content") else "names none"
        checks.append((f"{aid}: manifest_uri {leg}"
                       f"{' (' + want_uri.split('/')[-1] + ')' if want_uri else ''}",
                       got == want_uri, f"got {got!r}, want {want_uri!r}"))
    named = [a for a in by_id.values() if a.get("content")]
    checks.append((f"both legs of the new member are exhibited: "
                   f"{len(named)} anchor(s) name content and "
                   f"{len(by_id) - len(named)} name none",
                   0 < len(named) < len(by_id),
                   "one leg is unexercised, so the member has no served "
                   "counter-example"))
    # And the manifest an anchor names must be one the endpoint holds, or
    # §8.2 resolves nothing and the reference is decoration.
    held = set(gateway.load_manifests(ROOT / "samples" / "manifests"))
    dangling = sorted(served[a["anchor_id"]].data["manifest_uri"]
                      for a in named
                      if served[a["anchor_id"]].data["manifest_uri"] not in held)
    checks.append((f"every anchor-named manifest is committed and held "
                   f"({len(held)} manifest(s) in samples/manifests)",
                   not dangling, f"dangling: {dangling}"))

    # TopicMeta durability, the revision's other half.
    atopic = next(r.topic for r in st.resources.values()
                  if r.fqn == "spatial.disco.Announce")
    ann = next(iter(st.latched[atopic].values())).data
    want_dur = doc["service"]["topic_durability"]
    bad = [t for t in ann["topics"] if t.get("durability") != want_dur]
    checks.append((f"every TopicMeta entry carries durability "
                   f"{want_dur!r} from the input ({len(ann['topics'])} entry)",
                   not bad, f"{[(t['type'], t.get('durability')) for t in bad]}"))

    checks.append((f"frame_ref.uuid and .fqn are the input's",
                   fr["uuid"] == doc["frame_ref"]["uuid"]
                   and fr["fqn"] == doc["frame_ref"]["fqn"],
                   f"{fr['uuid']} {fr['fqn']}"))
    checks.append(("frame_ref declares metric scale, so coordinates mean "
                   "metres (§2.13)",
                   fr["has_scale"] is True
                   and fr["scale_status"] == "SCALE_DECLARED"
                   and fr["meters_per_unit"] == 1.0, f"{fr}"))

    # Time: int64 seconds arrive as a decimal string, per MAPPING.md.
    import calendar
    import datetime as dt
    t = dt.datetime.fromisoformat(doc["stamp_iso"].replace("Z", "+00:00"))
    want_sec = calendar.timegm(t.utctimetuple())
    checks.append((f"geopose.stamp.sec is the decimal string "
                   f"{str(want_sec)!r}",
                   g["stamp"]["sec"] == str(want_sec),
                   f"{g['stamp']['sec']!r}"))

    # --- structural checks across the whole set ----------------------------
    bad_conf = []
    for a in doc["anchors"]:
        sigma_max = max(a["sigma_enu_m"])
        expect = round(1.0 / (1.0 + sigma_max), 4)
        if served[a["anchor_id"]].data["confidence"] != expect:
            bad_conf.append(a["anchor_id"])
    checks.append(("every anchor's confidence is the monotone summary of its "
                   "covariance that GeoAnchor requires, 1/(1+sigma_max)",
                   not bad_conf, f"{bad_conf}"))

    ordered = sorted(doc["anchors"], key=lambda a: max(a["sigma_enu_m"]))
    confs = [served[a["anchor_id"]].data["confidence"] for a in ordered]
    checks.append(("confidence decreases as covariance grows, so the scalar "
                   "and the matrix never contradict",
                   confs == sorted(confs, reverse=True), f"{confs}"))

    places = {(round(served[a["anchor_id"]].data["geopose"]["lat_deg"], 7),
               round(served[a["anchor_id"]].data["geopose"]["lon_deg"], 7))
              for a in doc["anchors"]}
    checks.append((f"all {len(doc['anchors'])} anchors are at distinct "
                   f"positions", len(places) == len(doc["anchors"]),
                   f"{len(places)} distinct"))

    # Spread: a few metres apart, as the input intends. Guards against an
    # authoring bug that collapses the set or scatters it across a city.
    lats = [p[0] for p in places]
    lons = [p[1] for p in places]
    span_ns = (max(lats) - min(lats)) * 111320.0
    span_ew = (max(lons) - min(lons)) * 111320.0 * math.cos(
        math.radians(sum(lats) / len(lats)))
    checks.append((f"the set spans a plausible few metres "
                   f"({span_ew:.1f} m east-west, {span_ns:.1f} m north-south)",
                   1.0 < span_ns < 100.0 and 1.0 < span_ew < 100.0,
                   "the spread is not a fountain-sized footprint"))

    return report(f"golden anchor {GOLDEN}", checks,
                  "the recording carries exactly what the input declares")


if __name__ == "__main__":
    sys.exit(main())
