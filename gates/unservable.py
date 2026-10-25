#!/usr/bin/env python3
"""A recording carrying a non-finite double is refused by name, never served and never fatal unnamed.

N.1's JSON has no form for NaN or an infinity, and DDS carries both. The ruling
on finding 64: the gateway refuses such a recording whole, with a diagnostic
naming the topic, the instance key and the member. It does not drop the sample
and serve the rest, and it does not die on an uncaught `ValueError` from the
digest.

The gate authors its recordings from the pin with the gateway's own writer:

* a latched GeoAnchor with `geopose.lat_deg = NaN`. Before the ruling this
  died in the digest with no name;
* a streamed ModelPose with `pose.t[1] = inf`. Before the ruling this one
  loaded, because only latched samples are digested at load, and reached the
  wire as Python's `Infinity` token. It is the arm that a latched-only check
  would pass;
* the same two samples with finite values, as the control, which must load.

Then `spatialdds-serve --mcap` on the NaN recording, as an operator runs it:
it must exit non-zero with the diagnostic and no traceback.

Usage:  gates/unservable.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

from mcap.writer import Writer as McapWriter                   # noqa: E402

from harness import report                                     # noqa: E402
import spatialdds as sd                                        # noqa: E402
from spatialdds.server import gateway, pin, webjson, xcdr2     # noqa: E402

ANCHOR, POSE = "spatial.core.GeoAnchor", "spatial.owm.ModelPose"
A_TOPIC = "spatialdds/gate-nan/anchors/geo_anchor/v1"
P_TOPIC = "spatialdds/gate-nan/world/model_pose/v1"
A_KEY, P_KEY = "anchor/gate-nan", "ent:gate:nan"


def author(path: Path, lat: float, y: float) -> None:
    """One GeoAnchor and one ModelPose, encoded from the pin's own types."""
    recs = [pin.schema_record(n.replace(".", "::")) for n in (ANCHOR, POSE)]
    model, idl = pin.model_from_records(recs)
    lift, enc = webjson.Lifter(model, webjson.tables()), xcdr2.Writer(model)
    anchor = sd.geo_anchor(A_KEY, lat=30.28, lon=-97.73, method="SURVEYED")
    anchor["geopose"]["lat_deg"] = lat
    pose = sd.model_pose(P_KEY, position=(0.0, 0.0, 0.0), source_id="svc:gate")
    pose["pose"]["t"][1] = y
    with open(path, "wb") as f:
        w = McapWriter(f)
        w.start(profile="spatialdds", library="gates/unservable")
        for i, (fqn, topic, doc) in enumerate(((ANCHOR, A_TOPIC, anchor),
                                               (POSE, P_TOPIC, pose))):
            name, data = idl[fqn]
            sid = w.register_schema(name=name, encoding="omgidl", data=data)
            cid = w.register_channel(topic=topic, message_encoding="cdr",
                                     schema_id=sid)
            w.add_message(channel_id=cid, log_time=i + 1, publish_time=i + 1,
                          data=enc.encode(fqn, lift.lift(fqn, doc)))
        w.finish()


def refusal(path: Path):
    try:
        gateway.build_state(path, None)
    except gateway.RecordingRefused as e:
        return e
    except Exception as e:                                    # died unnamed
        return e
    return None


def run() -> int:
    checks: list = []
    with tempfile.TemporaryDirectory() as tmp:
        ok, nan, inf = (Path(tmp) / f"{n}.mcap" for n in ("ok", "nan", "inf"))
        author(ok, 30.28, 0.0)
        author(nan, float("nan"), 0.0)
        author(inf, 30.28, float("inf"))

        st = gateway.build_state(ok, None)
        checks.append(("the control, the same two samples with finite values, "
                       "loads and serves both topics",
                       {A_TOPIC, P_TOPIC} <= {r.topic for r in
                                              st.resources.values()},
                       f"{sorted(st.resources)}"))

        e = refusal(nan)
        checks.append(("a latched GeoAnchor with a NaN latitude is refused, "
                       "as RecordingRefused rather than an uncaught error",
                       isinstance(e, gateway.RecordingRefused), f"{e!r}"))
        checks.append(("…naming its topic, key and member",
                       isinstance(e, gateway.RecordingRefused)
                       and (e.topic, e.key, e.member)
                       == (A_TOPIC, A_KEY, "geopose.lat_deg")
                       and all(s in str(e) for s in
                               (A_TOPIC, A_KEY, "geopose.lat_deg", "nan")),
                       f"{e}"))

        e = refusal(inf)
        checks.append(("a streamed ModelPose with an infinite coordinate is "
                       "refused too, though nothing digests a stream sample",
                       isinstance(e, gateway.RecordingRefused)
                       and (e.topic, e.key, e.member)
                       == (P_TOPIC, P_KEY, "pose.t[1]"),
                       f"{e!r}"))

        cli = subprocess.run(
            [sys.executable, "-m", "spatialdds.serve", "--mcap", str(nan),
             "--port", "0"], capture_output=True, text=True, timeout=60,
            cwd=ROOT / "lib" / "spatialdds")
        checks.append(("spatialdds-serve on it exits non-zero with the "
                       "diagnostic and no traceback",
                       cli.returncode != 0 and "Traceback" not in cli.stderr
                       and A_TOPIC in cli.stderr
                       and "geopose.lat_deg" in cli.stderr,
                       f"rc={cli.returncode} {cli.stderr.strip()[-300:]}"))
    return report("unservable recordings", checks,
                  "a non-finite double refuses its recording by name")


if __name__ == "__main__":
    sys.exit(run())
