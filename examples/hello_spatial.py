"""Hello spatial: discover a SpatialDDS service and read typed samples.

Ten lines, `requests` and `websockets`, no DDS and no code from this
repository. Start an endpoint first -- the README's serve line is the one this
is written against -- then run this.

**This is the transparency exhibit.** `pip install spatialdds` does the same
six lines with typed values and verified bytes, and a reader is entitled to
ask what the library is doing on their behalf. It is this: one GET for the
service manifest, one URL followed out of it, one WebSocket, and the samples
read until the marker. If the library ever looks like it is doing something
clever, this file is the whole of it.

The shape is the point. Line 4 asks the discovery endpoint what is here and
gets a service manifest; line 5 picks a topic out of it; the rest subscribes to
that topic's own URL and reads typed anchors until the server says the latched
set is complete. Only the base URL is hardcoded: the path is followed, not
constructed.

Anchors, because the recording the README serves and the public endpoint serves
is the fountain one. If a topic is missing the script says which and what the
endpoint does offer, on one line: a newcomer's first thirty seconds should not
end in a traceback.
"""
import json, os, requests, sys, websockets.sync.client as wsc
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "http://127.0.0.1:8787"
try: hits = requests.get(f"{BASE}/.well-known/spatialdds/search", timeout=10).json()["results"]
except requests.RequestException as e: sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787")
topics = [t for r in hits for t in r["service"]["topics"]]
topic = next((t for t in topics if t["type"] == "geo_anchor"), None) or sys.exit(
    f'{BASE} serves no geo_anchor topic; it offers: {", ".join(sorted({t["type"] for t in topics}))}')
with wsc.connect(topic["url"].replace("http://", "ws://")) as ws:
    while (e := json.loads(ws.recv()))["event"] != "end_of_replay":
        g = e["data"]["geopose"]
        print(f'{e["key"]:30s} {e["data"]["method"]:9s} {g["lat_deg"]:.6f}, {g["lon_deg"]:.6f}')
print(f'latched set complete: {e["replayed"]} anchor(s)')
