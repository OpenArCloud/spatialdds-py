"""What anchors are here, and how sure is the publisher? Nine lines.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/anchors.py

Needs a recording that carries anchors: the fountain sample does. Covariance is
printed as the largest standard deviation, which is what `confidence`
summarises.
"""
import math, os, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
try: hits = requests.get(f"{BASE}/.well-known/spatialdds/search", timeout=10).json()["results"]
except requests.RequestException as e: sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787")
topics = [t for r in hits for t in r["service"]["topics"]]
topic = next((t for t in topics if t["type"] == "geo_anchor"), None) or sys.exit(
    f'{BASE} serves no geo_anchor topic; it offers: {", ".join(sorted({t["type"] for t in topics}))}')
for a in requests.get(topic["url"]).json():
    g, c = a["geopose"], a["cov"]
    worst = math.sqrt(max(c["pos"][0], c["pos"][4], c["pos"][8]))
    print(f'  {a["anchor_id"]:30s} {g["lat_deg"]:.6f},{g["lon_deg"]:.6f} '
          f'{a["method"]:9s} +/-{worst:.2f} m  conf {a["confidence"]}')
