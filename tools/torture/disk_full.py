#!/usr/bin/env python3
"""Fill the journal's filesystem; writes refuse cleanly, reads keep serving.

    python3 tools/torture/disk_full.py /small-fs

`/small-fs` must be a small filesystem of its own (the Docker wrapper mounts a
tmpfs there). A filler file takes most of it, then manifests are published
until one is refused. Asserted:

* the refusal is a 503 problem document (`journal-unwritable`), and every
  later write is too, while the disk stays full;
* reads serve exactly what they served before the refusal;
* the journal on disk is still a complete MCAP and replays to the accepted
  state (a restart serves the same bytes);
* removing the filler is the whole recovery: the next publish is a 201 with no
  restart and no operator action;
* no traceback in the service log.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Service, call, journal_valid, manifest, snapshot,  # noqa: E402
                    tokens, tracebacks, AUTH, SCENE)


def main() -> int:
    fs = Path(sys.argv[1])
    work = Path("/tmp/disk-full")
    work.mkdir(exist_ok=True)
    table, tok = tokens(work)
    log = work / "service.log"
    j = fs / "registry.mcap"
    svc = Service(j, table, 8991, log).start()
    filler = fs / "filler"
    # Take most of the space: everything but ~160 kB, enough for the first
    # journal writes (each rewrite needs the old file and the new one).
    import os
    st = os.statvfs(fs)
    free = st.f_bavail * st.f_frsize
    with open(filler, "wb") as f:
        f.write(b"\0" * max(0, free - 160 * 1024))
    accepted, uris, refusal = 0, [], None
    for i in range(10000):
        m = manifest(f"fill-{i:05d}", pad=600)
        code, h, body = call(svc.base, "PUT", "/spatialdds/resolve", m, tok)
        if code == 201:
            accepted += 1
            uris.append(m["id"])
            continue
        refusal = (code, h.get("Content-Type"), h.get("Retry-After"),
                   body.decode())
        break
    print(f"accepted        {accepted} manifest(s) before the disk filled")
    print(f"refusal         {refusal[0]} {refusal[1]} Retry-After={refusal[2]}")
    print(f"                {refusal[3]}")
    before = snapshot(svc.base, uris[-3:])
    again = [call(svc.base, "PUT", "/spatialdds/resolve",
                  manifest(f"again-{k}", 600), tok)[0] for k in range(5)]
    after = snapshot(svc.base, uris[-3:])
    reads_ok = before == after and all(c == 200 for c, _ in after.values())
    ok_mcap, why = journal_valid(j)
    print(f"while full      5 more writes -> {again}; reads unchanged: "
          f"{reads_ok}; journal valid MCAP: {ok_mcap} ({why})")
    svc.kill()
    svc = Service(j, table, 8991, log).start()
    replayed = snapshot(svc.base, uris[-3:])
    code_first, _h, _b = call(svc.base, "GET", "/spatialdds/resolve?uri="
                              + __import__("urllib.parse").parse.quote(
                                  uris[0], safe=""))
    print(f"after SIGKILL   restart serves the same bytes: {replayed == before}"
          f"; first accepted manifest resolves: {code_first}")
    filler.unlink()
    code, _h, body = call(svc.base, "PUT", "/spatialdds/resolve",
                          manifest("after-space-returns", 600), tok)
    print(f"space returns   next publish -> {code} (no restart, no operator "
          f"action)")
    svc.stop()
    tb = tracebacks(log)
    print(f"service log     {tb} traceback(s)")
    ok = (refusal and refusal[0] == 503
          and refusal[1].startswith("application/problem+json")
          and json.loads(refusal[3]).get("type", "").endswith(
              "journal-unwritable")
          and set(again) == {503} and reads_ok and ok_mcap
          and replayed == before and code_first == 200 and code == 201
          and tb == 0)
    print("PASS" if ok else "FAIL", "disk full: writes refuse with 503, reads "
          "serve, the journal stays whole, recovery is free space")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
