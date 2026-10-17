"""What zones are here? Six lines, against any binding endpoint.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/zones.py

**Needs the retail recording**, which is the only one here with zones in it:

    python3 tools/run_gateway.py --mcap samples/queuing-retail-sample.mcap \
        --port 8788

The fountain recording the README serves has no zones, and this script will
say so rather than fail obscurely.
"""
import os, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
try: hits = requests.get(f"{BASE}/.well-known/spatialdds/search", timeout=10).json()["results"]
except requests.RequestException as e: sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/queuing-retail-sample.mcap --port 8788")
pairs = [(r["service"], t) for r in hits for t in r["service"]["topics"]]
svc, topic = next(((s, t) for s, t in pairs if t["type"] == "spatial_zone"),
                  (None, None))
if topic is None: sys.exit(
    f'{BASE} serves no spatial_zone topic; it offers: {", ".join(sorted({t["type"] for _s, t in pairs}))}')
print(f'{len(requests.get(topic["url"]).json())} zone(s) at {svc["name"]}')
for z in requests.get(topic["url"]).json():
    print(f'  {z["kind"]:16s} {z["name"]:20s} {z["zone_id"]}')
