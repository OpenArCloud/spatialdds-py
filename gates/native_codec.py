#!/usr/bin/env python3
"""One JSON dialect, by construction: a native sample and a served one cannot differ.

`spatialdds.dds.to_json` / `from_json` are the gateway's own codec run on
native samples (ruling 5). This gate holds that to the specification's
vectors and to this package's builders.

* **Every golden vector**, the spec's authored N.1 for one sample of one type,
  lifts into a typed sample and projects back to exactly its `expected_json`.
  The typed sample serializes to the same bytes the gateway's writer encodes.
* **Every record builder's output** round-trips the same way, `GeoAnchor`
  included. That is the one SpatialDDS-demo's own mapper refused (`'type' is
  not one of its cases`), the two-dialect divergence the census measured.
* **Keys** are the gateway's instance names: `key_of(sample)` equals what
  `gateway.build_state` indexes the same bytes under.
* **Validation refuses**: a sample whose N.1 form breaks its published schema
  raises `SchemaInvalid` before anything is written.
* **Blobs**: a chunked blob reassembles. A flipped byte fails its chunk's
  CRC32, and a wrong whole-blob SHA-256 fails even when every CRC32 matched,
  which is the check openvps lacks.

Usage:  gates/native_codec.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from harness import report                                    # noqa: E402

import spatialdds as sd                                       # noqa: E402
from spatialdds.dds import codec                              # noqa: E402
from spatialdds.dds._participant import _types_class          # noqa: E402
from spatialdds.server import gateway, pin, webjson, xcdr2    # noqa: E402

GOLDEN = ROOT / "webjson" / "golden"


def roundtrip(fqn: str, doc: dict) -> tuple[bool, str]:
    cls = _types_class(fqn)
    try:
        typed = codec.from_json(cls, doc)
        back = codec.to_json(typed)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    _d, writer, _p, lifter = codec._codec()
    wire = writer.encode(fqn, lifter.lift(fqn, doc))
    if back != doc:
        diff = [k for k in doc if doc.get(k) != back.get(k)]
        return False, f"differs at {diff[:4]}"
    if typed.serialize() != wire:
        return False, "cyclonedds bytes differ from the gateway writer's"
    return True, ""


def main() -> int:
    checks: list = []
    vectors = sorted(GOLDEN.glob("*.json"))
    bad = []
    for p in vectors:
        v = json.loads(p.read_text())
        ok, why = roundtrip(v["type"].replace("::", "."), v["expected_json"])
        if not ok:
            bad.append(f"{p.stem}: {why}")
    checks.append((f"all {len(vectors)} golden vectors: N.1 → typed → N.1 is "
                   f"exactly expected_json, and the bytes are the gateway "
                   f"writer's", not bad, "; ".join(bad[:2])))

    cov = sd.circle(30.2849, -97.7341, 50)
    frame = sd.frame("map/plaza", convention="ENU", meters_per_unit=1.0)
    built = {
        "spatial.core.GeoAnchor": sd.geo_anchor(
            "a1", lat=30.28, lon=-97.73, alt_m=150.0, method="SURVEYED",
            sigma_enu_m=(0.02, 0.02, 0.05)),
        "spatial.disco.ContentAnnounce": sd.content_announce(
            "content/duck", coverage=cov, authority="r.example", scene="plaza"),
        "spatial.disco.Announce": sd.service_announce(
            "svc:x", "OTHER", coverage=cov, authority="r.example",
            scene="plaza", topics=[sd.topic("geopose", scene="plaza",
                                            stream="r", qos_profile="POSE_RT")]),
        "spatial.owm.Entity": sd.entity("ent:duck", frame_ref=frame,
                                        source_id="svc:x", position=(1, 2, 0),
                                        properties={"demo.label": "Duck"}),
        "spatial.owm.ModelPose": sd.model_pose("ent:duck", position=(1, 2, 0),
                                               source_id="svc:x"),
    }
    bad = []
    for fqn, doc in built.items():
        ok, why = roundtrip(fqn, doc)
        if not ok:
            bad.append(f"{fqn}: {why}")
    checks.append((f"every record builder's output ({len(built)} types, "
                   f"GeoAnchor among them) round-trips the same way",
                   not bad, "; ".join(bad[:2])))

    # Keys, against the gateway's own indexing of the same bytes.
    from mcap.writer import Writer as McapWriter
    with tempfile.TemporaryDirectory() as tmp:
        rec = Path(tmp) / "k.mcap"
        model, idl = pin.wire_model()
        samples = [codec.from_json(_types_class(f), d) for f, d in built.items()
                   if f in idl]
        with open(rec, "wb") as f:
            w = McapWriter(f)
            w.start(profile="spatialdds", library="gates/native_codec")
            for i, s in enumerate(samples):
                fqn = codec.fqn_of(s)
                name, data = idl[fqn]
                sid = w.register_schema(name=name, encoding="omgidl", data=data)
                cid = w.register_channel(
                    topic=f"spatialdds/plaza/k{i}/{fqn.rsplit('.', 1)[1].lower()}/v1",
                    message_encoding="cdr", schema_id=sid)
                w.add_message(channel_id=cid, log_time=i + 1, publish_time=i + 1,
                              data=s.serialize())
            w.finish()
        st = gateway.build_state(rec, None)
        served = sorted(k for t in st.latched.values() for k in t)
    native = sorted(codec.key_of(s) for s in samples)
    checks.append((f"key_of names each instance exactly as the gateway "
                   f"indexes the same bytes ({', '.join(native)})",
                   native == served and len(native) == len(samples),
                   f"native {native} / gateway {served}"))

    def refusal(mutate) -> str:
        e = codec.from_json(_types_class("spatial.owm.Entity"),
                            built["spatial.owm.Entity"])
        mutate(e)
        try:
            codec.validate(e)
            return ""
        except codec.SchemaInvalid as x:
            return f"SchemaInvalid: {x}"
        except Exception as x:
            return f"{type(x).__name__}: {x}"
    wire = refusal(lambda e: setattr(e.pose, "q", [0.0, 0.0, 0.0]))
    checks.append(("a sample that cannot be its type's wire form (a "
                   "3-element quaternion) is refused before it is written",
                   bool(wire) and not wire.startswith("SchemaInvalid"),
                   wire[:120]))
    nan = refusal(lambda e: setattr(e.pose, "t", [float("nan"), 0.0, 0.0]))
    checks.append(("a NaN, which DDS carries and N.1 does not, is refused as "
                   "SchemaInvalid by N.1's own finite-only rule",
                   nan.startswith("SchemaInvalid") and "N.1" in nan, nan[:140]))

    data = os.urandom(150_000)
    ref = sd.dds.blob_ref("b1", "image", data)
    chunks = list(sd.dds.chunk("b1", data))
    r = sd.dds.Reassembler()
    r.expect("b1", ref["checksum"])
    whole = [r.feed(c) for c in chunks][-1]
    checks.append((f"a {len(data)}-byte blob in {len(chunks)} chunks "
                   f"reassembles, checked whole against its BlobRef",
                   whole == data and not r.unverified, ""))
    flipped = list(sd.dds.chunk("b2", data))
    flipped[1].data[0] ^= 1
    try:
        [sd.dds.Reassembler().feed(c) for c in flipped]
        crc = ""
    except sd.dds.CorruptChunk as e:
        crc = str(e)
    checks.append(("a flipped byte fails its chunk's CRC32", "CRC32" in crc,
                   crc))
    r3 = sd.dds.Reassembler()
    r3.expect("b1", "sha256:" + "0" * 64)
    try:
        [r3.feed(c) for c in chunks]
        sha = ""
    except sd.dds.CorruptChunk as e:
        sha = str(e)
    checks.append(("a wrong whole-blob SHA-256 fails even with every CRC32 "
                   "matching", "whole blob" in sha, sha[:100]))
    return report("native codec", checks,
                  "one JSON dialect: a native sample and a served one cannot "
                  "disagree")


if __name__ == "__main__":
    sys.exit(main())
