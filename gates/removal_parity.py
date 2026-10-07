#!/usr/bin/env python3
"""Every removal is a terminal sample then a dispose, for the same key.

§2.14's shape, checked as stated: the terminal sample first, carrying the
instance's last state and a reason, then the dispose **immediately** after it.
Not a dispose alone, which tells a client the instance is gone without saying
what it last was, and not a dispose later in the stream with other events
between, because then a client cannot tell which sample was terminal.

The expected number is derived from the recording: a removal corresponds to a
sample whose lifecycle state is RETIRED, counted directly over the decoded
messages rather than taken from the gateway's own bookkeeping.

Usage:  gates/removal_parity.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

from harness import Gateway, MCAP, TOKEN, report, state        # noqa: E402
from replay_order import collect                               # noqa: E402
from spatialdds_web import gateway, idlmodel, webjson, xcdr2    # noqa: E402

from mcap.reader import make_reader                            # noqa: E402


def expected_retirements() -> dict[str, int]:
    """Terminal samples per topic, counted straight off the recording."""
    import json
    model = gateway.wire_model(MCAP)
    tables = json.loads((ROOT / "webjson" / "serializer_tables.json").read_text())
    dec, proj = xcdr2.Decoder(model), webjson.Projector(model, tables)
    out: dict[str, int] = {}
    with open(MCAP, "rb") as f:
        for sch, ch, msg in make_reader(f).iter_messages():
            fqn = sch.name.replace("::", ".")
            d = proj.project(fqn, dec.decode(fqn, msg.data))
            if d.get("state") == "RETIRED":
                out[ch.topic] = out.get(ch.topic, 0) + 1
    return out


async def run() -> int:
    exp = expected_retirements()
    st = state()
    gw = await Gateway(st).start()
    checks: list[tuple[str, bool, str]] = []
    total = 0

    for path in sorted(st.resources):
        res = st.resources[path]
        if res.topic not in exp and not res.latched:
            continue
        want = exp.get(res.topic, 0)
        if want == 0:
            continue
        evs = await collect(gw.base.replace("http://", "ws://") + path, TOKEN)
        at = next((i for i, e in enumerate(evs)
                   if e.get("event") == "end_of_replay"), None)
        live = evs[at + 1:] if at is not None else evs
        idx = [i for i, e in enumerate(live) if e.get("event") == "dispose"]
        total += len(idx)
        label = res.scene[:8]
        checks.append((f"{label}: {len(idx)} dispose(s) for the {want} "
                       f"terminal sample(s) in the recording",
                       len(idx) == want, f"{len(idx)} vs {want}"))
        order, st_bad, reason_bad = [], [], []
        for i in idx:
            prev = live[i - 1] if i > 0 else None
            d = live[i]
            if (prev is None or prev.get("event") != "sample"
                    or prev.get("key") != d.get("key")):
                order.append(d.get("key"))
                continue
            if (prev.get("data") or {}).get("state") != "RETIRED":
                st_bad.append(d.get("key"))
            if not d.get("reason"):
                reason_bad.append(d.get("key"))
        checks.append((f"{label}: every dispose immediately follows a sample "
                       f"for the same key", not order, f"{order[:3]}"))
        checks.append((f"{label}: that sample is terminal, state RETIRED",
                       not st_bad, f"{st_bad[:3]}"))
        checks.append((f"{label}: every dispose carries a reason",
                       not reason_bad, f"{reason_bad[:3]}"))
        keys = [live[i].get("key") for i in idx]
        checks.append((f"{label}: no key disposed twice",
                       len(keys) == len(set(keys)), ""))
        still = [k for k in keys if k in st.latched.get(res.topic, {})]
        checks.append((f"{label}: no disposed key is still served over GET",
                       not still, f"{still[:3]}"))

    await gw.stop()
    checks.append((f"{total} dispose(s) against {sum(exp.values())} terminal "
                   f"sample(s) in the recording",
                   total == sum(exp.values()), ""))
    checks.append(("the recording contains removals, so this is not comparing "
                   "zero with zero", sum(exp.values()) > 0, ""))
    return report("removal parity", checks,
                  "removal is terminal sample then dispose")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
