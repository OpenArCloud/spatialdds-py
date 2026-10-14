"""The SSD loop, whole: publish a service, then read it back both ways.

`examples/migration/ssd_search.py` reads a registry; this writes to one.
Together they are the round trip OSCP had — register a Spatial Service Record,
then discover it.

Both halves, because an announce carries a `manifest_uri` and publishing the
announce alone leaves that URI dangling: the obvious next thing a reader does
is resolve it, and it would answer "No manifest for that URI". So this script
publishes the announce *and* the manifest it names, then retrieves both ways —
search finds the service, and the URI the announce advertises resolves.

Zero prerequisites. Nothing else need have run first.

    export SPATIALDDS_ENDPOINT=http://127.0.0.1:8787
    export SPATIALDDS_TOKEN=tok
    python3 examples/publish_service.py
"""
import json, os, pathlib, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit(
    "  set SPATIALDDS_TOKEN: the surface is operator-scoped; the gateway's banner prints the export line"))}
d = json.loads((pathlib.Path(__file__).parents[1] / "samples/published-records.input.json").read_text())
rec, man = d["service"], d["service_manifest"]
try:
    a = requests.put(f'{BASE}{rec["path"]}/{rec["key"]}', json=rec["record"], headers=AUTH, timeout=10)
    m = requests.put(f"{BASE}/spatialdds/resolve", json=man, headers=AUTH, timeout=10)
except requests.RequestException as e:
    sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 --public-url https://demo.spatialdds.org --token tok --publish")
{a.status_code, m.status_code} <= {200, 201} or sys.exit(f'  refused: announce {a.status_code}, manifest {m.status_code}. 405 means no --publish, 401 a wrong token')
print(f'  PUT announce  -> {a.status_code}  {rec["key"]}')
print(f'  PUT manifest  -> {m.status_code}  {man["id"]}')
print("  GET search    -> " + ", ".join(sorted(s["service"]["service_id"] for s in
      requests.get(f"{BASE}/.well-known/spatialdds/search", timeout=10).json()["results"])))
back = requests.get(f"{BASE}/spatialdds/resolve", params={"uri": rec["record"]["manifest_uri"]}, timeout=10).json()
print(f'  GET resolve   -> {back.get("rtype")} {back.get("id")} ttl {back.get("ttl_sec")}')
print("\n" + json.dumps(back, indent=2))   # the record itself, as the resolver returned it
