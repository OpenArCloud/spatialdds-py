"""What zones are here? Six lines, against any binding endpoint.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/zones.py

Needs a recording that carries zones: the retail sample does.
"""
import os, requests
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
svc = requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"][0]
topic = next(t for t in svc["service"]["topics"] if t["type"] == "spatial_zone")
print(f'{len(requests.get(topic["url"]).json())} zone(s) at {svc["service"]["name"]}')
for z in requests.get(topic["url"]).json():
    print(f'  {z["kind"]:16s} {z["name"]:20s} {z["zone_id"]}')
