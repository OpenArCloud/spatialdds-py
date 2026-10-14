"""Publish a content record and its manifest, then resolve it back.

The SCD successor in the write direction: `examples/migration/scd_content.py`
resolves a content URI, this one creates what it resolves to. A content record
needs both halves -- the announce that says where the content is, and the
manifest the announce's `manifest_uri` points at -- so both are published, and
the last line is the resolver answering for a URI that did not exist a moment
earlier.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 SPATIALDDS_TOKEN=... \\
        python3 examples/publish_content.py
"""
import json, os, pathlib, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit(
    "set SPATIALDDS_TOKEN: the publish surface is operator-scoped"))}
doc = json.loads((pathlib.Path(__file__).parents[1] / "samples/published-records.input.json").read_text())
con, man = doc["content"], doc["manifest"]
coll = BASE + con["path"]
try:
    a = requests.put(f'{coll}/{con["key"].replace("/", "%2F")}', json=con["record"], headers=AUTH, timeout=10)
    m = requests.put(BASE + "/spatialdds/resolve", json=man, headers=AUTH, timeout=10)
except requests.RequestException as e:
    sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 --public-url https://demo.spatialdds.org --token tok --publish")
{a.status_code, m.status_code} <= {200, 201} or sys.exit(f'  refused: announce {a.status_code}, manifest {m.status_code}. 405 means no --publish, 401 a wrong token')
print(f'  PUT announce  -> {a.status_code}  {con["key"]}')
print(f'  PUT manifest  -> {m.status_code}  {man["id"]}')
print("  GET list      -> " + ", ".join(sorted(c["content_id"] for c in requests.get(coll, timeout=10).json())))
back = requests.get(BASE + "/spatialdds/resolve", params={"uri": man["id"]}, timeout=10)
print(f'  GET resolve   -> {back.json()["rtype"]} {back.json()["id"]} ttl {back.json()["ttl_sec"]} -> {back.headers["Cache-Control"]}')
print("\n" + json.dumps(back.json(), indent=2))   # the record itself, as the resolver returned it
