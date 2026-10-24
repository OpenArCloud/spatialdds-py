#!/usr/bin/env python3
"""Soak on the box's size: publish, update, withdraw, sampled throughout.

    python3 tools/torture/soak.py [CYCLES]

Each cycle publishes a new manifest, replaces it, and withdraws it -- three
journalled writes -- against the real service in a 512 MB container. Every
`EVERY` cycles it samples the service's RSS, the journal's size, and the mean
write latency over the cycles since the last sample, and prints one CSV row.

The point beyond "it does not fall over" is pricing finding 40. The journal
keeps every entry and rewrites the whole file on every write, so a write's
cost grows with history, not state (state here stays at the seed: everything
published is withdrawn). The rows show where that cost crosses a threshold
worth stating: the journal size at which one write takes longer than
`THRESHOLD_MS`.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Service, call, manifest, tokens, tracebacks  # noqa: E402

EVERY = 100
THRESHOLD_MS = 100.0


def main() -> int:
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    work = Path("/tmp/soak")
    work.mkdir(exist_ok=True)
    table, tok = tokens(work)
    log = work / "service.log"
    j = work / "registry.mcap"
    svc = Service(j, table, 8993, log).start()
    print("cycle,writes,journal_bytes,rss_kb,mean_write_ms,max_write_ms")
    print(f"0,0,{j.stat().st_size if j.exists() else 0},{svc.rss_kb()},0,0")
    lat: list[float] = []
    crossed = None
    errors = 0
    t_start = time.monotonic()
    for c in range(1, cycles + 1):
        m = manifest(f"soak-{c:06d}", pad=300)
        for verb, body, path in (
                ("PUT", m, "/spatialdds/resolve"),
                ("PUT", {**m, "content": {**m["content"], "title": "again"}},
                 "/spatialdds/resolve"),
                ("DELETE", None, "/spatialdds/resolve?uri="
                 + quote(m["id"], safe=""))):
            t0 = time.monotonic()
            code, _h, _b = call(svc.base, verb, path, body, tok, timeout=60)
            lat.append((time.monotonic() - t0) * 1000)
            errors += code >= 300
        if c % EVERY == 0:
            mean, mx = sum(lat) / len(lat), max(lat)
            size = j.stat().st_size
            print(f"{c},{c * 3},{size},{svc.rss_kb()},{mean:.1f},{mx:.1f}",
                  flush=True)
            if crossed is None and mean > THRESHOLD_MS:
                crossed = (c, size, mean)
            lat = []
    svc.stop()
    dur = time.monotonic() - t_start
    print(f"# {cycles} cycles, {cycles * 3} writes in {dur:.0f} s, "
          f"{errors} non-2xx, {tracebacks(log)} traceback(s)")
    if crossed:
        print(f"# mean write latency first exceeded {THRESHOLD_MS:.0f} ms at "
              f"cycle {crossed[0]}: journal {crossed[1]} bytes, "
              f"{crossed[2]:.1f} ms")
    else:
        print(f"# mean write latency stayed under {THRESHOLD_MS:.0f} ms")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
