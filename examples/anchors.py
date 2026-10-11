"""What anchors are here, and how sure is the publisher? Nine lines.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/anchors.py

Needs a recording that carries anchors: the fountain sample does. Covariance is
printed as the largest standard deviation, which is what `confidence`
summarises.
"""
import math, os, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
svc = requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"][0]["service"]
topic = next((t for t in svc["topics"] if t["type"] == "geo_anchor"), None) or sys.exit(
    f'{BASE} serves no geo_anchor topic; it offers: {", ".join(t["type"] for t in svc["topics"])}')
for a in requests.get(topic["url"]).json():
    g, c = a["geopose"], a["cov"]
    worst = math.sqrt(max(c["pos"][0], c["pos"][4], c["pos"][8]))
    print(f'  {a["anchor_id"]:30s} {g["lat_deg"]:.6f},{g["lon_deg"]:.6f} '
          f'{a["method"]:9s} +/-{worst:.2f} m  conf {a["confidence"]}')
