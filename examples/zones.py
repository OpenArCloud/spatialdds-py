"""What zones are here? Six lines, against any binding endpoint.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/zones.py

**Needs the retail recording**, which is the only one here with zones in it:

    python3 tools/run_gateway.py --mcap samples/queuing-retail-sample.mcap \
        --port 8788

The fountain recording the README serves has no zones, and this script will
say so rather than fail obscurely.
"""
import os, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
svc = requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"][0]["service"]
topic = next((t for t in svc["topics"] if t["type"] == "spatial_zone"), None) or sys.exit(
    f'{BASE} serves no spatial_zone topic; it offers: {", ".join(t["type"] for t in svc["topics"])}')
print(f'{len(requests.get(topic["url"]).json())} zone(s) at {svc["name"]}')
for z in requests.get(topic["url"]).json():
    print(f'  {z["kind"]:16s} {z["name"]:20s} {z["zone_id"]}')
