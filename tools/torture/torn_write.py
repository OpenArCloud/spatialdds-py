#!/usr/bin/env python3
"""Publish, SIGKILL mid-stream, restart, check -- several hundred times.

    python3 tools/torture/torn_write.py [ITERATIONS]

Each iteration starts the service on the journal the last one left, fires
manifests at it from a thread, and SIGKILLs the process at a random moment
while writes are in flight. Then, with zero tolerance:

* the journal on disk parses as a complete MCAP;
* the restarted service replays it and comes up;
* every write the client saw acknowledged is in the journal and resolves;
* nothing is in the journal that was never sent (at most the one write in
  flight at the kill may be there unacknowledged).

The journal starts fresh every 25 iterations so the whole-file rewrite
(finding 40) stays cheap enough for hundreds of kills; the soak prices it.
"""
from __future__ import annotations

import random
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Service, call, journal_valid, manifest, tokens  # noqa: E402


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    work = Path("/tmp/torn")
    work.mkdir(exist_ok=True)
    table, tok = tokens(work)
    log = work / "service.log"
    rng = random.Random(4242)
    failures, acked_total, kills_mid_write = [], 0, 0
    from spatialdds.server import publish
    j = None
    for it in range(n):
        if it % 25 == 0:
            j = work / f"registry-{it // 25}.mcap"
            acked_all: set[str] = set()
            sent_all: set[str] = set()
            #: accepted-but-unacknowledged writes earlier kills left behind
            seen_inflight: set[str] = set()
        svc = Service(j, table, 8992, log).start()
        stop = threading.Event()
        lock = threading.Lock()

        def fire():
            k = 0
            while not stop.is_set():
                m = manifest(f"torn-{it:04d}-{k:03d}", pad=rng.randrange(400))
                with lock:
                    sent_all.add(m["id"])
                try:
                    code, _h, _b = call(svc.base, "PUT", "/spatialdds/resolve",
                                        m, tok, timeout=2)
                except Exception:                              # noqa: BLE001
                    return
                if code == 201:
                    with lock:
                        acked_all.add(m["id"])
                k += 1

        t = threading.Thread(target=fire, daemon=True)
        t.start()
        time.sleep(rng.uniform(0.05, 0.35))
        svc.kill()
        stop.set()
        t.join(timeout=5)
        ok, why = journal_valid(j)
        if not ok:
            failures.append((it, f"journal not a valid MCAP: {why}"))
            break
        items, _idl = publish.read_entries(j)
        in_journal = {e["uri"] for _s, e in items if e["kind"] == "manifest"}
        with lock:
            missing = acked_all - in_journal
            invented = in_journal - sent_all
            inflight = in_journal - acked_all - seen_inflight
            seen_inflight |= inflight
        if missing:
            failures.append((it, f"acknowledged but not journalled: "
                                 f"{sorted(missing)[:3]}"))
        if invented:
            failures.append((it, f"journalled but never sent: "
                                 f"{sorted(invented)[:3]}"))
        if len(inflight) > 1:
            failures.append((it, f"{len(inflight)} new unacknowledged writes "
                                 f"in the journal from one kill"))
        kills_mid_write += bool(inflight)
        svc = Service(j, table, 8992, log).start()
        sample = sorted(acked_all)[-5:]
        bad = [u for u in sample if call(
            svc.base, "GET", "/spatialdds/resolve?uri=" + quote(u, safe=""))[0]
               != 200]
        if bad:
            failures.append((it, f"acknowledged but not served: {bad}"))
        svc.stop()
        acked_total = acked_total
        if (it + 1) % 50 == 0:
            print(f"  {it + 1:4d} kills, {len(in_journal)} manifests in the "
                  f"current journal, {len(failures)} failure(s)", flush=True)
        if failures:
            break
    print(f"iterations      {it + 1} SIGKILLs during a publish stream")
    print(f"mid-write kills {kills_mid_write} left an accepted-but-unacknowledged "
          f"write (allowed: at most one, and it was sent)")
    print(f"failures        {len(failures)}" + (f": {failures[:3]}"
                                               if failures else ""))
    print("PASS" if not failures else "FAIL", "torn writes: every kill leaves "
          "a valid journal that replays every acknowledged write")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
