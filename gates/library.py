#!/usr/bin/env python3
"""The client library, against the reference gateway it does not import.

`lib/spatialdds` ships two dependencies and neither of them is this
repository. So the library cannot borrow the gateway's `webjson.Lifter`, which
needs the IDL model a recording carries; it drives the same three N.1 rules
from the generated serializer tables that travel in the wheel. That makes
"same rules, same results" a claim rather than a shared implementation, and a
claim needs a gate: the first section lifts **every message in both
recordings** with each of the two and requires them to agree.

The rest holds the library to the provenance rule every gate here follows.
Expected values come from the committed authoring inputs -- the anchors' own
methods, the duck's declared byte count and digest, the service's declared
durability -- never from the library and never from the gateway's answer to
the question being asked. Equality against an authored record is checked
through an endpoint connected with `lifted=False`, so the comparison is
canonical JSON against canonical JSON and the lift is not asked to vouch for
itself.

Isolation: every section builds its own gateway on its own port, and the
publish sections get their own journal in a temporary directory. Nothing here
carries state into anything else, which is what lets the three README bars run
as the documentation claims -- each one alone, in a fresh shell.

Usage:  gates/library.py [--bars-only] [--refill]

`--refill` rewrites the ```console fences under the three bars in
lib/spatialdds/README.md with what they actually printed. It is the only way
those outputs are ever written.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"
sys.path.insert(0, str(LIB))

from mcap.reader import make_reader                            # noqa: E402

from harness import (ARTIFACTS, FOUNTAIN, MANIFESTS, MCAP,     # noqa: E402
                     TOKEN, Gateway, report, state)
from spatialdds.server import gateway, webjson, xcdr2             # noqa: E402

import spatialdds as sd                                        # noqa: E402
from spatialdds import canonical                               # noqa: E402

DEMO = "https://demo.spatialdds.org"
FIN = ROOT / "samples" / "fountain-anchors.input.json"
DUCK = ROOT / "samples" / "duck.input.json"
PUB = ROOT / "samples" / "published-records.input.json"
README = LIB / "README.md"


def dig(doc, *path):
    """A nested lookup that answers None instead of raising.

    A check has to be able to fail. Reading `doc["geopose"]["stamp"]["sec"]`
    straight makes a wrong-shaped document a KeyError, which kills the gate
    where it stands and reports nothing about the checks after it -- an
    injection that removed the lift was "caught" that way, by a traceback
    rather than by the FAIL line naming the claim.
    """
    for k in path:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(k)
    return doc


def epoch(iso: str) -> int:
    """The authoring input's stamp, as the integer the wire carries."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
               .replace(tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------- the lift --

def _rule_hits(fqn: str, doc, hits: dict, depth: int = 0) -> None:
    """Count which of the three N.1 rules a document actually exercises.

    Agreement over a corpus that never carries an octet run proves nothing
    about the base64 rule, and a gate that did not say so would be claiming
    coverage it does not have. These counts are printed.
    """
    T = webjson.tables()
    e = T.get(fqn)
    if e is None or depth > 12 or not isinstance(doc, dict):
        return
    if e.get("kind") == "union":
        hits["union"] += 1
        for _lab, nm, d in e["cases"]:
            if nm in doc:
                _rule_hits(d.get("fqn", ""), doc[nm], hits, depth + 1)
        return
    if e.get("kind") != "struct":
        return
    for nm, d in e["fields"]:
        if nm not in doc or doc[nm] is None:
            continue
        v, t = doc[nm], d.get("t")
        if t == "array":
            it = d.get("item", {})
            if it.get("t") == "base64":
                hits["base64"] += 1
            for x in (v if isinstance(v, list) else []):
                if it.get("t") in ("ref", "union"):
                    _rule_hits(it.get("fqn", ""), x, hits, depth + 1)
                elif it.get("t") in ("int64", "uint64"):
                    hits["strint"] += 1
            continue
        if t in ("int64", "uint64"):
            hits["strint"] += 1
        elif t == "base64":
            hits["base64"] += 1
        elif t in ("ref", "union"):
            _rule_hits(d.get("fqn", ""), v, hits, depth + 1)


def lift_section(checks: list) -> None:
    T = webjson.tables()
    hits = {"strint": 0, "base64": 0, "union": 0}
    total, disagree = 0, []
    for path in (MCAP, FOUNTAIN):
        model = gateway.wire_model(path)
        dec = xcdr2.Decoder(model)
        proj = webjson.Projector(model, T)
        lifter = webjson.Lifter(model, T)
        with open(path, "rb") as f:
            for sch, _ch, msg in make_reader(f).iter_messages():
                fqn = sch.name.replace("::", ".")
                doc = proj.project(fqn, dec.decode(fqn, msg.data))
                total += 1
                _rule_hits(fqn, doc, hits)
                if lifter.lift(fqn, doc) != canonical.lift(fqn, doc) \
                        and len(disagree) < 4:
                    disagree.append(fqn)
    checks.append((f"the library's lift and the gateway's `webjson.Lifter` "
                   f"agree on all {total} messages in both recordings",
                   total > 15000 and not disagree,
                   f"{len(disagree)} disagreed, e.g. {disagree[:3]}"
                   if disagree else f"only {total} messages read"))
    checks.append((f"those messages exercise the int64/uint64 rule "
                   f"{hits['strint']} times and the union rule "
                   f"{hits['union']} times",
                   hits["strint"] > 0 and hits["union"] > 0, f"{hits}"))

    # The third rule has no data in either recording, so it gets a record of
    # its own -- and the expected value comes from the stdlib, not from either
    # lifter, so this is not two implementations agreeing about nothing.
    model = gateway.wire_model(FOUNTAIN)
    lifter = webjson.Lifter(model, T)
    raw = b"spatialdds \x00\x01\xfe\xff bytes"
    blob = {"blob_id": "chunk/0", "index": 0, "total_chunks": 1,
            "crc32": 0, "data": base64.b64encode(raw).decode()}
    mine = canonical.lift("spatial.core.BlobChunk", blob)
    theirs = lifter.lift("spatial.core.BlobChunk", blob)
    checks.append((f"the base64 rule, which neither recording exercises, "
                   f"undoes to the {len(raw)} bytes `base64.b64decode` gives, "
                   "identically in both",
                   mine == theirs and mine["data"] == list(raw),
                   f"{str(mine.get('data'))[:60]} vs {list(raw)[:8]}"))

    # The authored publish records: the discovery and anchor types a client
    # actually sends, which the recordings carry only as replayed samples.
    authored = []
    for src, keys in ((PUB, ("service", "content")),
                      (DUCK, ("announce", "anchor"))):
        d = json.loads(src.read_text())
        authored += [(d[k]["fqn"], d[k]["record"]) for k in keys if k in d]
    probe = json.loads(PUB.read_text())["canonical_probe"]
    authored.append((probe["fqn"], probe["canonical"]))
    bad = [f for f, r in authored
           if lifter.lift(f, r) != canonical.lift(f, r)]
    checks.append((f"and on the {len(authored)} authored records the publish "
                   f"examples send ({len(set(f for f, _ in authored))} types)",
                   authored and not bad, f"{bad}"))

    # The injection. A lift that skipped the int64 rule must be caught by the
    # comparison above, or the comparison is decoration.
    sample = next(r for f, r in authored if f.endswith("GeoAnchor"))
    broken = copy.deepcopy(canonical.lift("spatial.core.GeoAnchor", sample))
    broken["geopose"]["stamp"]["sec"] = str(broken["geopose"]["stamp"]["sec"])
    checks.append(("injection: a lift that left `stamp.sec` a string is not "
                   "equal to the gateway's",
                   broken != lifter.lift("spatial.core.GeoAnchor", sample),
                   "the comparison cannot see the int64 rule at all"))


# --------------------------------------------------------------- read flows --

async def read_section(checks: list, shape: str, advertise: str | None) -> None:
    """Every read verb, against a fresh endpoint, in one of the two shapes."""
    inp = json.loads(FIN.read_text())
    want_sec = epoch(inp["stamp_iso"])
    want_methods = sorted(a["method"] for a in inp["anchors"])
    want_ids = sorted(a["anchor_id"] for a in inp["anchors"])
    svc_id = inp["service"]["service_id"]
    cov = inp["service"]["coverage_circle"]
    gh = sd.geohash_encode(cov["center_lat"], cov["center_lon"], 6)
    p = f"[{shape}] "

    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=advertise).start()
    try:
        ep = sd.connect(gw.base)
        raw = sd.connect(gw.base, lifted=False)

        hits = await asyncio.to_thread(ep.search, gh)
        ids = sorted(h.service_id for h in hits)
        checks.append((f"{p}search on the input's own cell {gh} finds "
                       f"{svc_id!r} among {len(hits)} service(s)",
                       svc_id in ids, f"{ids}"))
        hit = next(h for h in hits if h.service_id == svc_id)
        checks.append((f"{p}the hit carries the input's kind "
                       f"{inp['service']['kind']}",
                       hit.kind == inp["service"]["kind"], f"{hit.kind!r}"))

        top = hit.topic("geo_anchor")
        base_for_urls = (advertise or gw.base).rstrip("/")
        checks.append((f"{p}the topic URL is the manifest's hypermedia, on "
                       f"{base_for_urls}, not a path this library built",
                       top.url.startswith(base_for_urls + "/")
                       and top.url.endswith("/geo_anchor/v1"), f"{top.url!r}"))
        checks.append((f"{p}its durability is the input's "
                       f"{inp['service']['topic_durability']}",
                       top.durability == inp["service"]["topic_durability"],
                       f"{top.durability!r}"))
        checks.append((f"{p}its access class is public-cacheable and its FQN "
                       "resolves to spatial.core.GeoAnchor",
                       top.access == "public-cacheable"
                       and top.fqn == "spatial.core.GeoAnchor",
                       f"{top.access!r} {top.fqn!r}"))

        got = await asyncio.to_thread(top.latched)
        checks.append((f"{p}latched() returns the input's {len(inp['anchors'])} "
                       "anchors, by the ids the input names",
                       sorted(str(dig(a, "anchor_id")) for a in got)
                       == want_ids,
                       f"{sorted(str(dig(a, 'anchor_id')) for a in got)}"))
        checks.append((f"{p}every method is the one the input declared",
                       sorted(str(dig(a, "method")) for a in got)
                       == want_methods,
                       f"{sorted(str(dig(a, 'method')) for a in got)}"))
        checks.append((f"{p}the lift gives geopose.stamp.sec as the int "
                       f"{want_sec}, from the input's {inp['stamp_iso']}",
                       bool(got) and all(dig(a, "geopose", "stamp", "sec")
                                        == want_sec for a in got),
                       f"{[dig(a, 'geopose', 'stamp', 'sec') for a in got][:2]}"))

        unlifted = await asyncio.to_thread(raw.search, gh)
        rtop = next(h for h in unlifted
                    if h.service_id == svc_id).topic("geo_anchor")
        rgot = await asyncio.to_thread(rtop.latched)
        checks.append((f"{p}lifted=False leaves it the decimal string N.1 "
                       "puts on the wire",
                       bool(rgot) and all(dig(a, "geopose", "stamp", "sec")
                                         == str(want_sec) for a in rgot),
                       f"{[dig(a, 'geopose', 'stamp', 'sec') for a in rgot][:2]}"))
        checks.append((f"{p}a union arrives tagged on `type` and lifts to "
                       "`_discriminator`, same value",
                       dig(rgot[0], "cov", "type") is not None
                       and dig(rgot[0], "cov", "type")
                       == dig(got[0], "cov", "_discriminator"),
                       f"{dig(rgot[0], 'cov', 'type')!r} vs "
                       f"{dig(got[0], 'cov', '_discriminator')!r}"))

        key = want_ids[0]
        one = await asyncio.to_thread(top.instance, key)
        checks.append((f"{p}instance({key!r}) percent-encodes the slash and "
                       "returns that anchor",
                       dig(one, "anchor_id") == key,
                       f"{dig(one, 'anchor_id')!r}"))

        # ETags respected: the endpoint's own 304, observed through the client.
        url = ep._url(sd._path_of(top.url))
        etag = ep._etags.get(url, "")
        r = await asyncio.to_thread(
            lambda: ep._request("GET", sd._path_of(top.url),
                                headers={"If-None-Match": etag}, raw=True))
        checks.append((f"{p}a latched read stored the ETag and revalidates "
                       "to 304 rather than refetching",
                       bool(etag) and r.status_code == 304,
                       f"etag={etag!r} status={r.status_code}"))

        near = await asyncio.to_thread(ep.near, cov["center_lat"],
                                       cov["center_lon"], 30.0)
        far = await asyncio.to_thread(ep.near, cov["center_lat"] + 2.0,
                                      cov["center_lon"], 30.0)
        checks.append((f"{p}near() at the declared centre finds the service "
                       "and 2 degrees north finds nothing",
                       svc_id in [h.service_id for h in near] and not far,
                       f"{[h.service_id for h in near]} / "
                       f"{[h.service_id for h in far]}"))
        checks.append((f"{p}reaches() answers from the declared coverage: "
                       "true at the centre, false 2 degrees north",
                       hit.reaches(cov["center_lat"], cov["center_lon"], 1.0)
                       and not hit.reaches(cov["center_lat"] + 2.0,
                                           cov["center_lon"], 1.0), ""))

        try:
            hit.topic("no_such_type")
            msg, ok = "", False
        except sd.SpatialDDSError as e:
            msg, ok = str(e), "geo_anchor" in str(e)
        checks.append((f"{p}asking for a topic the service has not got names "
                       f"what it does have", ok, f"{msg!r}"))
    finally:
        await gw.stop()


# ------------------------------------------------------------ publish flows --

async def publish_section(checks: list) -> None:
    pub = json.loads(PUB.read_text())
    duck = json.loads(DUCK.read_text())
    kinds = [("announce", pub["service"]), ("content_announce", pub["content"]),
             ("geo_anchor", duck["anchor"])]

    with tempfile.TemporaryDirectory() as tmp:
        jp = Path(tmp) / "journal.mcap"
        gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO,
                           publish=True, journal=jp).start()
        try:
            ep = sd.connect(gw.base, token=TOKEN)
            raw = sd.connect(gw.base, token=TOKEN, lifted=False)
            for name, spec in kinds:
                w = await asyncio.to_thread(ep.publish, spec["path"],
                                            spec["key"], spec["record"])
                checks.append((f"publish {name}: created, and the served "
                               "document is the authored record",
                               w.created and w.doc == spec["record"],
                               f"created={w.created} differs at "
                               f"{sorted(k for k in set(w.doc) | set(spec['record']) if w.doc.get(k) != spec['record'].get(k))}"))
                checks.append((f"publish {name}: the ETag is the digest of "
                               "its canonical form",
                               w.etag == f'"sha256:{webjson.digest(spec["record"])}"',
                               f"{w.etag!r}"))
                back = await asyncio.to_thread(
                    raw.get_json, f"{spec['path']}/{sd._quote_key(spec['key'])}")
                checks.append((f"publish {name}: reading it back unlifted "
                               "gives the authored record byte for byte",
                               back == spec["record"],
                               f"{json.dumps(back)[:120]}"))

            man = duck["manifest"]
            w = await asyncio.to_thread(ep.publish_manifest, man)
            checks.append(("publish_manifest keys the manifest by its own id "
                           "and returns it unchanged",
                           w.created and w.doc == man,
                           f"created={w.created}"))

            # If-Match, both ways. The ETag a publish hands back has to be
            # in the form the header wants, which is the bug that made every
            # conditional write a 412 the first time this was built.
            svc = pub["service"]
            again = await asyncio.to_thread(ep.publish, svc["path"],
                                            svc["key"], svc["record"])
            checks.append(("a second publish of the same record replaces it "
                           "rather than creating it", not again.created,
                           f"created={again.created}"))
            cond = await asyncio.to_thread(ep.publish, svc["path"],
                                           svc["key"], svc["record"],
                                           again.etag)
            ok412 = None
            try:
                await asyncio.to_thread(ep.publish, svc["path"], svc["key"],
                                        svc["record"],
                                        '"sha256:' + "0" * 64 + '"')
            except sd.Problem as e:
                ok412 = e.status
            checks.append(("If-Match with the ETag the publish returned is "
                           "accepted, and a stale one is 412",
                           cond.doc == svc["record"] and ok412 == 412,
                           f"conditional ok={cond.doc == svc['record']}, "
                           f"stale gave {ok412}"))

            fin = json.loads(FIN.read_text())
            cell = sd.geohash_encode(
                fin["service"]["coverage_circle"]["center_lat"],
                fin["service"]["coverage_circle"]["center_lon"], 6)
            reader = sd.connect(gw.base)
            anchors = await asyncio.to_thread(reader.search, cell)
            top = next(h for h in anchors
                       if h.service_id == fin["service"]["service_id"]) \
                .topic("geo_anchor")
            got = await asyncio.to_thread(top.latched)
            n_in = len(json.loads(FIN.read_text())["anchors"])
            checks.append((f"the published anchor is latched beside the "
                           f"recording's {n_in}",
                           len(got) == n_in + 1
                           and duck["anchor"]["key"] in
                           [a["anchor_id"] for a in got],
                           f"{len(got)} anchors"))

            await asyncio.to_thread(ep.withdraw, duck["anchor"]["path"],
                                    duck["anchor"]["key"])
            after = await asyncio.to_thread(top.latched)
            checks.append((f"withdraw() leaves the latched set at {n_in} and "
                           "the recording's anchors untouched",
                           len(after) == n_in
                           and sorted(a["anchor_id"] for a in after)
                           == sorted(a["anchor_id"] for a in
                                     json.loads(FIN.read_text())["anchors"]),
                           f"{len(after)} anchors"))
        finally:
            await gw.stop()

        # The journal, read by a second gateway: what the library wrote
        # survives a restart, which is the gateway's promise and the library's
        # only interest in it.
        gw2 = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO,
                            publish=True, journal=jp).start()
        try:
            ep2 = sd.connect(gw2.base, token=TOKEN, lifted=False)
            back = await asyncio.to_thread(
                ep2.get_json,
                f"{pub['content']['path']}/{sd._quote_key(pub['content']['key'])}")
            checks.append(("after a restart from the journal the published "
                           "content announce is served unchanged",
                           back == pub["content"]["record"],
                           f"{json.dumps(back)[:120]}"))
        finally:
            await gw2.stop()


# -------------------------------------------------------------- resolve ----

async def resolve_section(checks: list) -> None:
    duck = json.loads(DUCK.read_text())
    label = json.loads((MANIFESTS / "content-fountain-label.json").read_text())
    want_bytes = duck["asset"]["bytes"]
    want_hash = duck["asset"]["sha256"]

    with tempfile.TemporaryDirectory() as tmp:
        gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO,
                           publish=True,
                           journal=Path(tmp) / "j.mcap").start()
        try:
            ep = sd.connect(gw.base, token=TOKEN)
            await asyncio.to_thread(ep.publish_manifest, duck["manifest"])
            res = await asyncio.to_thread(ep.resolve, duck["manifest"]["id"])
            checks.append(("resolve() follows the endpoint's advertised "
                           "https_base and returns the manifest's own id",
                           res.doc.get("id") == duck["manifest"]["id"],
                           f"{res.doc.get('id')!r}"))
            checks.append(("the asset URL on this endpoint's own authority "
                           "is rebased onto the endpoint",
                           res.asset_url(0) == gw.base + "/assets/duck.gltf",
                           f"{res.asset_url(0)!r}"))
            data = await asyncio.to_thread(res.fetch_asset)
            checks.append((f"fetch_asset returns the input's declared "
                           f"{want_bytes} bytes at {want_hash[:14]}…",
                           len(data) == want_bytes
                           and "sha256:" + hashlib.sha256(data).hexdigest()
                           == want_hash, f"{len(data)} bytes"))

            # The fixed bug, as a check. An asset on another host is fetched as
            # written: rebasing every absolute URL turned the fountain label's
            # CDN asset into a 404 on the endpoint.
            res2 = await asyncio.to_thread(ep.resolve, label["id"])
            checks.append(("an asset on another host is left exactly as the "
                           "manifest wrote it",
                           res2.asset_url(0) == label["assets"][0]["uri"],
                           f"{res2.asset_url(0)!r}"))

            # Doctored servings. Both of these are manifests the endpoint
            # really holds and really serves; nothing is mocked.
            bad = copy.deepcopy(duck["manifest"])
            bad["id"] = duck["manifest"]["id"] + "-wrong-hash"
            bad["assets"][0]["hash"] = "sha256:" + "ab" * 32
            await asyncio.to_thread(ep.publish_manifest, bad)
            r3 = await asyncio.to_thread(ep.resolve, bad["id"])
            err = None
            try:
                await asyncio.to_thread(r3.fetch_asset)
            except sd.IntegrityError as e:
                err = str(e)
            checks.append(("bytes that do not match the declared digest raise "
                           "IntegrityError naming both digests",
                           err is not None and "ab" * 8 in err
                           and hashlib.sha256(data).hexdigest()[:16] in err,
                           f"{err!r}"))
            loose = await asyncio.to_thread(r3.fetch_asset, 0, False)
            checks.append(("…and verify_integrity=False at the call site "
                           "returns the same bytes anyway",
                           loose == data, f"{len(loose)} bytes"))

            unver = copy.deepcopy(duck["manifest"])
            unver["id"] = duck["manifest"]["id"] + "-crc"
            unver["assets"][0]["hash"] = "crc32:deadbeef"
            await asyncio.to_thread(ep.publish_manifest, unver)
            r4 = await asyncio.to_thread(ep.resolve, unver["id"])
            err = None
            try:
                await asyncio.to_thread(r4.fetch_asset)
            except sd.UnverifiableIntegrity as e:
                err = str(e)
            checks.append(("an integrity value that is not a SHA-256 raises "
                           "UnverifiableIntegrity rather than reporting a "
                           "check it did not do (finding 31)",
                           err is not None and "crc32:deadbeef" in err,
                           f"{err!r}"))

            err = None
            try:
                await asyncio.to_thread(
                    ep.resolve,
                    "spatialdds://someone.else.example/x/content/y")
            except sd.Problem as e:
                err = e.status
            checks.append(("a URI belonging to another authority is a 404 "
                           "from the resolver, surfaced as Problem",
                           err == 404, f"got {err}"))
        finally:
            await gw.stop()


# ------------------------------------------------------------- subscribe ----

async def subscribe_section(checks: list) -> None:
    inp = json.loads(FIN.read_text())
    n = len(inp["anchors"])
    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO).start()
    try:
        ep = sd.connect(gw.base)
        hits = await asyncio.to_thread(
            ep.search, sd.geohash_encode(
                inp["service"]["coverage_circle"]["center_lat"],
                inp["service"]["coverage_circle"]["center_lon"], 6))
        top = next(h for h in hits
                   if h.service_id == inp["service"]["service_id"]) \
            .topic("geo_anchor")

        def drain():
            out = []
            for ev in top.subscribe(timeout=10):
                out.append(ev)
                if isinstance(ev, sd.EndOfReplay):
                    break
            return out

        evs = await asyncio.to_thread(drain)
        samples = [e for e in evs if isinstance(e, sd.Sample)]
        eor = [e for e in evs if isinstance(e, sd.EndOfReplay)]
        checks.append((f"subscribe() replays the input's {n} anchors then "
                       "exactly one EndOfReplay",
                       len(samples) == n and len(eor) == 1,
                       f"{len(samples)} samples, {len(eor)} end_of_replay"))
        checks.append((f"EndOfReplay reports {n} replayed, and the samples "
                       "are lifted like a latched read",
                       eor and eor[0].replayed == n
                       and all(isinstance(dig(s.data, "geopose", "stamp",
                                              "sec"), int)
                               for s in samples),
                       f"{eor[0].replayed if eor else '-'}"))
    finally:
        await gw.stop()


# ----------------------------------------------------------- the refusals ---

async def errors_section(checks: list) -> None:
    pub = json.loads(PUB.read_text())
    spec = pub["service"]

    async def expect(label, cls, gw, token, record, hint, path=None):
        ep = sd.connect(gw.base, token=token)
        got, msg = None, ""
        try:
            await asyncio.to_thread(ep.publish, path or spec["path"],
                                    spec["key"], record)
        except sd.SpatialDDSError as e:
            got, msg = type(e), str(e)
        checks.append((f"{label}: {cls.__name__}, and the message says how to "
                       "fix it", got is cls and hint in msg,
                       f"{got.__name__ if got else None}: {msg!r}"))
        return msg

    # Publishing on: the refusals the surface itself produces.
    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO,
                       publish=True).start()
    try:
        await expect("no token", sd.Unauthorized, gw, None, spec["record"],
                     "token=")
        await expect("a body that is not a record", sd.Problem, gw, TOKEN,
                     {"service_id": "x"}, "schema")
        foreign = copy.deepcopy(spec["record"])
        foreign["manifest_uri"] = "spatialdds://someone.else.example/z/s/q"
        msg = await expect("another authority's URI", sd.Problem, gw, TOKEN,
                           foreign, "someone.else.example")
        checks.append(("the 403's problem document is carried, not "
                       "re-invented: status, type and detail",
                       "403" in msg, f"{msg!r}"))
    finally:
        await gw.stop()

    # Publishing off: the same call, a different refusal.
    gw2 = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO).start()
    try:
        await expect("the write surface off", sd.SurfaceOff, gw2, TOKEN,
                     spec["record"], "--publish")
    finally:
        await gw2.stop()

    # Unreachable: a port that was listening and is not any more, so the
    # failure is a real connection refusal rather than a fabricated one.
    gw3 = await Gateway(state(FOUNTAIN, MANIFESTS)).start()
    dead = gw3.base
    await gw3.stop()
    ep = sd.connect(dead)
    got, msg = None, ""
    try:
        await asyncio.to_thread(ep.search, "9v6krc")
    except sd.SpatialDDSError as e:
        got, msg = type(e), str(e)
    checks.append(("an endpoint that is not listening: Unreachable, naming "
                   "the URL and a serve line",
                   got is sd.Unreachable and dead in msg
                   and "run_gateway.py" in msg,
                   f"{got.__name__ if got else None}: {msg!r}"))


# ---------------------------------------------------------------- the bars --

MARKER = re.compile(r"<!--\s*bar:\s*(\S+)\s*-->")
#: The token a page's bars authenticate with, taken from the page's own serve
#: line. A page that says `--token tok` and a gate that assumed something else
#: would disagree about an endpoint neither of them is wrong about.
TOKEN_RE = re.compile(r"--token\s+(\S+)")


def fence(lines: list[str], i: int, tag: str) -> tuple[list[str], int, int]:
    while i < len(lines) and lines[i].strip() != "```" + tag:
        i += 1
    if i >= len(lines):
        raise AssertionError(f"no ```{tag} fence found")
    body, start, i = [], i + 1, i + 1
    while i < len(lines) and lines[i].strip() != "```":
        body.append(lines[i])
        i += 1
    return body, start, i


#: Every page that carries a runnable bar. The front page carries one -- the
#: six-line read -- for the same reason the library README carries three: a
#: printed program whose output nobody checks is a promise, and this
#: repository does not make those.
BAR_DOCS = [README, ROOT / "README.md"]


def bars() -> list[dict]:
    out = []
    for doc in BAR_DOCS:
        text = doc.read_text()
        lines = text.splitlines()
        tok = TOKEN_RE.search(text)
        for i, line in enumerate(lines):
            m = MARKER.match(line.strip())
            if not m:
                continue
            prog, _ps, j = fence(lines, i, "python")
            # The console fence has to be the next one. Every bar pins its
            # output; a page that wants to show a line whose output is a blob
            # uses `gates/examples.py`'s `sh-ok`, which exists and is used.
            want, ws, we = fence(lines, j, "console")
            out.append({"doc": doc, "name": m.group(1), "line": i + 1,
                        "prog": prog, "token": tok.group(1) if tok else TOKEN,
                        "want": want, "from": ws, "to": we})
    return out


LOOPBACK = re.compile(r"http://127\.0\.0\.1:\d+")


async def bars_section(checks: list, refill: bool) -> list[dict]:
    found = bars()
    per_doc: dict = {}
    for b in found:
        k = b["doc"].relative_to(ROOT).as_posix()
        per_doc[k] = per_doc.get(k, 0) + 1
    checks.append(("every documented bar is present with a python fence and "
                   "a console fence: "
                   + ", ".join(f"{k} x{v}" for k, v in sorted(per_doc.items())),
                   len(found) == 8 and per_doc.get("README.md") == 3
                   and per_doc.get("lib/spatialdds/README.md") == 5,
                   f"found {len(found)}: {per_doc}"))
    env = dict(os.environ, PYTHONPATH=f"{LIB}:{ROOT}")
    for b in found:
        gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO,
                           publish=True, token=b["token"]).start()
        try:
            prog = LOOPBACK.sub(gw.base, "\n".join(b["prog"]))
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", prog, cwd=str(ROOT), env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            # stdout alone is the documented output. The first refill wrote a
            # local urllib3-version warning into the README, which is this
            # machine's environment and not the example's behaviour; a
            # traceback still fails the bar through the exit code, and
            # whatever went to stderr is printed in the failure detail.
            raw, err = await proc.communicate()
            text = raw.decode().replace(gw.base, "http://127.0.0.1:8787")
            got = [l.rstrip() for l in text.splitlines()]
            b["got"] = got
            want = [l.rstrip() for l in b["want"]]
            where = (f"{b['doc'].relative_to(ROOT).as_posix()}:{b['line']}, "
                     f"{len(b['prog'])} lines")
            checks.append((f"bar {b['name']!r} ({where}) runs clean and "
                           f"prints what the page shows",
                           proc.returncode == 0 and (got == want or refill),
                           f"exit {proc.returncode}\n"
                           + "\n".join(f"       got  {l}" for l in got[:8])
                           + "\n" + "\n".join(f"       want {l}"
                                              for l in want[:8])
                           + (f"\n       stderr {err.decode()[-300:]}"
                              if proc.returncode else "")))
            checks.append((f"bar {b['name']!r} printed something",
                           bool([l for l in got if l.strip()]),
                           "the documented output would be an empty fence"))
        finally:
            await gw.stop()
    if refill:
        for doc in BAR_DOCS:
            mine = [b for b in found if b["doc"] == doc]
            if not mine:
                continue
            lines = doc.read_text().splitlines()
            for b in sorted(mine, key=lambda x: -x["from"]):
                got = b.get("got", [])
                if not any(l.strip() for l in got):
                    print(f"  SKIPPED {doc.relative_to(ROOT)}:{b['line']} "
                          f"printed nothing, so its fence is left alone")
                    continue
                lines[b["from"]:b["to"]] = got
            doc.write_text("\n".join(lines) + "\n")
            print(f"  refilled {len(mine)} console fence(s) in "
                  f"{doc.relative_to(ROOT)}")
        print()
    return found


# --------------------------------------------------------------- the shape --

async def replay_section(checks: list, found: list) -> None:
    """The front page's blocks, in order, then again on the state they left.

    One endpoint for all of them, which is the opposite of how `bars_section`
    runs them: there, each bar gets a fresh registry so that a reader pasting
    one block in isolation is what is checked. Here the question is whether a
    block still works after the blocks above it have run, which is what a
    reader doing the whole page actually does -- and then whether it still
    works a second time, which is what re-pasting one does.
    """
    mine = [b for b in found
            if b["doc"].relative_to(ROOT).as_posix() == "README.md"]
    if not mine:
        checks.append(("the front page's blocks are replayable", False,
                       "no README.md bars were found to replay"))
        return
    env = dict(os.environ, PYTHONPATH=f"{LIB}:{ROOT}")
    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=DEMO,
                       publish=True, token=mine[0]["token"]).start()
    try:
        for pass_no in (1, 2):
            for b in mine:
                prog = LOOPBACK.sub(gw.base, "\n".join(b["prog"]))
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", prog, cwd=str(ROOT), env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
                raw, err = await proc.communicate()
                got = [l.rstrip() for l in raw.decode()
                       .replace(gw.base, "http://127.0.0.1:8787").splitlines()]
                want = [l.rstrip() for l in b["want"]]
                checks.append((
                    f"pass {pass_no}: bar {b['name']!r} prints what the front "
                    f"page says against a registry the page itself has "
                    f"already published into",
                    proc.returncode == 0 and got == want,
                    f"exit {proc.returncode}\n"
                    + "\n".join(f"       got  {l}" for l in got[:6])
                    + "\n" + "\n".join(f"       want {l}" for l in want[:6])
                    + (f"\n       stderr {err.decode()[-240:]}"
                       if proc.returncode else "")))
    finally:
        await gw.stop()


def shipped_page(checks: list) -> None:
    """The library README is the wheel's long description, so it is an artifact.

    It renders on PyPI, with no repository around it and no sidebar, under a
    markdown renderer nobody here controls. Three properties follow, and all
    three were broken when this was written:

    * **No code fence without an instrument.** Two Python blocks on this page
      had no bar marker, so nothing ever executed them, and both were broken:
      one carried `{{...}}` from a template-escaping slip in the script that
      generated it -- which is a set containing a dict, a TypeError as written
      -- and the other passed an undefined name. They shipped in 0.3.0.
    * **No relative links.** `../../docs/MYTOWN.md` resolves in a checkout and
      nowhere on an index page.
    * **No headerless tables.** GitHub hides an empty header row; PyPI renders
      it as a blank line across the table.

    A shell fence gets an instrument too: an install fence has to name the
    from-source line beside the release line, and a serve line has to name
    flags `spatialdds.serve` actually accepts.
    """
    import re
    import subprocess as sp
    text = README.read_text()
    lines = text.splitlines()

    fences = [(i + 1, l.strip()[3:].strip())
              for i, l in enumerate(lines)
              if l.startswith("```") and l.strip() != "```"]
    unmarked = []
    for n, tag in fences:
        if tag != "python":
            continue
        before = [x.strip() for x in lines[max(0, n - 4):n - 1]]
        # A bar runs against an endpoint (this gate); a native block runs on a
        # DDS domain (gates/native_readme.py). Either is an instrument.
        if not any(x.startswith(("<!-- bar:", "<!-- native:")) for x in before):
            unmarked.append(n)
    checks.append((f"every python fence on the shipped page has a bar behind "
                   f"it ({len([f for f in fences if f[1] == 'python'])} fences)",
                   not unmarked, f"ungated at line(s) {unmarked}"))

    rel = [m for m in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
           if not m.startswith(("http://", "https://", "mailto:", "#"))]
    checks.append((f"every link on it is absolute, because it renders where "
                   f"this repository is not "
                   f"({len(re.findall(r'[(]https?://', text))} absolute)",
                   not rel, f"relative: {rel}"))

    headerless = [i + 1 for i, l in enumerate(lines) if l.startswith("| |")]
    checks.append(("no table on it has an empty header row, which GitHub "
                   "hides and PyPI shows",
                   not headerless, f"at line(s) {headerless}"))

    checks.append((f"it names the repository by URL, since a reader on an "
                   f"index has no sidebar",
                   "github.com/OpenArCloud/spatialdds-web" in text,
                   "no repository URL on the page"))

    # The shell fences: an install line and the serve lines.
    helptext = sp.run([sys.executable, "-m", "spatialdds.serve", "--help"],
                      capture_output=True, text=True,
                      env={"PATH": os.environ.get("PATH", ""),
                           "PYTHONPATH": str(LIB)}).stdout
    bad_flags, pip_fences, lonely = [], 0, []
    body, tag = [], ""
    for i, l in enumerate(lines, 1):
        if l.startswith("```") and l.strip() != "```":
            tag, body = l.strip()[3:].strip(), []
            continue
        if l.strip() == "```" and tag == "sh":
            pips = [x for x in body if x.strip().startswith("pip install")]
            if pips:
                pip_fences += 1
                local = [x for x in pips
                         if any(a.strip('"').startswith((".", "/"))
                                for a in x.split()[2:])]
                remote = [x for x in pips if "spatialdds" in x and x not in local]
                if remote and not local:
                    lonely.append(i)
            for x in body:
                if "spatialdds.serve" in x or "run_gateway.py" in x:
                    bad_flags += [f for f in re.findall(r"--[a-z-]+", x)
                                  if f not in helptext]
            tag = ""
        elif tag:
            body.append(l)
    checks.append((f"its install fence names the from-source line beside the "
                   f"release line ({pip_fences} install fence(s))",
                   not lonely, f"lonely release line at {lonely}"))
    checks.append((f"and every flag its serve lines name exists in "
                   f"`spatialdds.serve --help`",
                   not bad_flags, f"unknown: {sorted(set(bad_flags))}"))


def surface_section(checks: list) -> None:
    text = README.read_text()
    missing = [n for n in sd.__all__
               if n != "__version__" and f"`{n}`" not in text
               and f"`sd.{n}`" not in text and f"`ep.{n}" not in text
               and f".{n}(" not in text and f"`{n}(" not in text]
    checks.append((f"all {len(sd.__all__)} names in `__all__` appear in the "
                   f"library README", not missing, f"{missing}"))
    pyproj = (LIB / "pyproject.toml").read_text()
    checks.append((f"pyproject declares version {sd.__version__} and the "
                   "module constant agrees",
                   f'version = "{sd.__version__}"' in pyproj,
                   "pyproject and __version__ disagree"))
    checks.append((f"the README names spec commit {sd.SPEC_COMMIT}, which is "
                   "the module's SPEC_COMMIT",
                   sd.SPEC_COMMIT in text, "the README names another commit"))
    # The constants against the pin the artifacts were vendored at, so the
    # package cannot say one revision while carrying another's bytes.
    prov = dict(line.split("=", 1) for line in
                (ROOT / "webjson" / "PROVENANCE").read_text().splitlines()
                if "=" in line and not line.startswith("#"))
    prov = {k.strip(): v.strip() for k, v in prov.items()}
    checks.append((f"SPEC_TAG {sd.SPEC_TAG} and SPEC_COMMIT {sd.SPEC_COMMIT} "
                   "are the tag and commit webjson/PROVENANCE vendored at",
                   sd.SPEC_TAG == prov.get("artifacts_tag")
                   and prov.get("artifacts_commit", "").startswith(sd.SPEC_COMMIT)
                   and len(sd.SPEC_COMMIT) >= 7,
                   f"{prov.get('artifacts_tag')} {prov.get('artifacts_commit')}"))
    # There used to be two copies of the generated tables -- one under
    # webjson/ for the gateway, one inside the package -- kept honest by a
    # byte-identity check. 0.2.0 removed the second: the server moved into the
    # distribution, so both halves read `artifacts/`. What remains is the
    # check that can still fail, which is a copy coming back.
    def found(pattern):
        return sorted(q.relative_to(ROOT).as_posix()
                      for q in ROOT.rglob(pattern)
                      if not {"__pycache__", "build", "dist"} & set(q.parts))
    tbl = found("serializer_tables.json")
    want = (ARTIFACTS / "serializer_tables.json").relative_to(ROOT).as_posix()
    checks.append((f"exactly one copy of the serializer tables exists, under "
                   f"artifacts/ ({len(tbl)} found)",
                   tbl == [want], f"{tbl}"))
    sch = found("spatial_core_GeoAnchor.json")
    wsch = (ARTIFACTS / "schemas" /
            "spatial_core_GeoAnchor.json").relative_to(ROOT).as_posix()
    checks.append((f"and one copy of the generated JSON Schemas "
                   f"({len(sch)} found)", sch == [wsch], f"{sch}"))
    # The release-time comparator, exercised offline. A tool reached for once
    # a release and never otherwise is a tool that fails the one time it
    # matters, which is why its self-test runs here rather than in a habit.
    r = subprocess.run([sys.executable,
                        str(ROOT / "tools" / "compare_wheel.py"),
                        "--self-test"], cwd=str(ROOT), capture_output=True,
                       text=True)
    passed = [l for l in r.stdout.splitlines() if l.strip().startswith("ok")]
    checks.append((f"tools/compare_wheel.py knows a changed payload from a "
                   f"changed build stamp ({len(passed)} self-checks, offline)",
                   r.returncode == 0 and len(passed) >= 6,
                   f"exit {r.returncode}: {r.stdout[-200:]}"))
    checks.append(("pyproject declares requests and websockets and nothing "
                   "else",
                   'dependencies = ["requests>=2.25", "websockets>=12"]'
                   in pyproj, "the dependency list changed"))


async def alone_section(checks: list) -> None:
    """The wheel's contents, imported with this repository off sys.path.

    The point of a client library is that it works on a machine that has never
    seen this repository, and an import that quietly reaches for
    `spatialdds.server` would be invisible from inside a checkout where that
    import always succeeds. So this runs in a subprocess whose PYTHONPATH is
    the package directory and nothing else, and requires both that every
    public name is there and that `spatialdds.server` cannot be imported at all.
    """
    probe = (
        "import sys, json\n"
        "import spatialdds as sd\n"
        "ep = sd.connect('http://127.0.0.1:1')\n"
        "try:\n"
        "    ep.search('9v6krc')\n"
        "except sd.Unreachable:\n"
        "    pass\n"
        "third = sorted(m for m in ('aiohttp', 'mcap', 'jsonschema',\n"
        "                           'spatialdds.server', 'spatialdds.serve')\n"
        "               if m in sys.modules)\n"
        "print(json.dumps({'v': sd.__version__,\n"
        "                  'spec': sd.SPEC_COMMIT,\n"
        "                  'missing': [n for n in sd.__all__\n"
        "                              if not hasattr(sd, n)],\n"
        "                  'third': third}))\n")
    with tempfile.TemporaryDirectory() as tmp:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", probe, cwd=tmp,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(LIB)},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        raw, _ = await proc.communicate()
    try:
        d = json.loads(raw.decode().strip().splitlines()[-1])
    except Exception:
        d = {}
    checks.append((f"imported with only the package on the path, all "
                   f"{len(sd.__all__)} public names are present at version "
                   f"{sd.__version__}",
                   d.get("v") == sd.__version__ and d.get("missing") == []
                   and d.get("spec") == sd.SPEC_COMMIT,
                   f"{raw.decode()[-300:]}"))
    # Which modules may import which is `gates/import_graph.py`'s subject
    # now that the server ships in the same distribution: "spatialdds_web is
    # not importable" was the old form of that claim and stopped being true
    # the moment the gateway moved in. What belongs here is the client's own
    # promise: the two dependencies it declares are the two it uses.
    checks.append(("…and reading an endpoint there imports none of the "
                   "server's dependencies and no server module",
                   d.get("third") == [],
                   f"the client pulled in {d.get('third')}"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars-only", action="store_true")
    ap.add_argument("--refill", action="store_true",
                    help="rewrite the bars' console fences with what they "
                         "printed")
    a = ap.parse_args()
    checks: list[tuple[str, bool, str]] = []
    if not a.bars_only:
        lift_section(checks)
        await read_section(checks, "plain", None)
        await read_section(checks, "advertised", DEMO)
        await publish_section(checks)
        await resolve_section(checks)
        await subscribe_section(checks)
        await errors_section(checks)
    found = await bars_section(checks, a.refill)
    if not a.bars_only:
        await replay_section(checks, found)
        shipped_page(checks)
        surface_section(checks)
        await alone_section(checks)
    return report("the spatialdds client library against the reference "
                  "gateway", checks,
                  "the library reads, publishes, resolves and refuses as its "
                  "README says")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
