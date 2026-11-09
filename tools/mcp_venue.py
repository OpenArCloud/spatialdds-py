#!/usr/bin/env python3
"""Capture a venue that serves its world lanes public, for the MCP gates.

The public sandbox serves no world lane to a reader without a token (its
world lanes are operator-scoped, and today none is open), so the MCP server's
world tools have nothing to read there. SpatialDDS-demo's venue
(`bridges/web_bridge/binding.py`) classifies its world lanes public, by its
own judgement of what they carry. This reads one, the way the MCP server
does, without a token, and writes what it served:

    python3 tools/mcp_venue.py http://127.0.0.1:8089 samples/mcp/venue.json

Every public latched lane's records, verbatim, and every lane's manifest
entry, so the gates' copy (`gates/_mcp.py`, `Venue`) serves the same lanes
with the same access classes. The capture's time and source go in the file:
a copy is evidence of what was served then, and says so.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import spatialdds as sd                                      # noqa: E402


def main() -> int:
    base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
    ep = sd.connect(base, lifted=False)
    hits = ep.search()
    if len(hits) != 1:
        raise SystemExit(f"expected one service at {base}, found {len(hits)}")
    svc = hits[0].service
    lanes = []
    for t in svc["topics"]:
        path = urlsplit(t["url"]).path
        lane = {"topic": t, "path": path}
        if t.get("access") == "public-cacheable" \
                and t.get("durability") == "TRANSIENT_LOCAL":
            r = ep._request("GET", path, raw=True)
            lane["fqn"] = r.headers.get("X-SpatialDDS-Type", "")
            lane["records"] = r.json()
        lanes.append(lane)
    doc = {"captured_from": base, "captured_at": time.strftime(
               "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "service": {k: v for k, v in svc.items() if k != "topics"},
           "lanes": lanes}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"{out}: {sum(len(l.get('records', [])) for l in lanes)} records "
          f"in {sum(1 for l in lanes if 'records' in l)} public latched "
          f"lane(s) of {len(lanes)}, from {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
