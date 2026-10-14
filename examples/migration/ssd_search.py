"""OSCP Spatial Service Discovery, and what replaces it. Runs today.

Predecessor: OSCP SSD, `GET /ssrs?h3Index=<cell>&topic=<t>`, returning Spatial
Service Records. The successor is the web binding's §3.3.0 search path, which
answers the same question with typed service manifests and no bus membership.
Clients convert their H3 cell to a geohash locally; the privacy posture is
unchanged, since the client still sends a cell rather than its position.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/migration/ssd_search.py
"""
import os, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
CELL = os.environ.get("SPATIALDDS_GEOHASH", "9v6krc")
print(f"  was:  GET /ssrs?h3Index=8a2a1072b59ffff      -> Spatial Service Records")
print(f"  now:  GET /.well-known/spatialdds/search?geohash={CELL}")
try: hits = requests.get(f"{BASE}/.well-known/spatialdds/search", params={"geohash": CELL}, timeout=10).json()["results"]
except requests.RequestException as e: sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787")
for m in hits:
    s = m["service"]
    print(f'\n  {s["service_id"]}  kind={s["kind"]}  org={s["org"]}')
    for t in s["topics"]:
        print(f'    {t["type"]:18s} {t["durability"]:16s} {t["access"]:17s} {t["url"]}')
