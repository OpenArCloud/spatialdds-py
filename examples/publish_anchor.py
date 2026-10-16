"""The open-anchors gesture in two PUTs: publish content, then anchor it.

The 1.8 revision gave `GeoAnchor` an optional `manifest_uri`, so an anchor can
name a content manifest. This publishes the duck's manifest and then an anchor
that names it, and reads both back: the anchor beside the recording's five, the
reference resolved, and the whole anchor document printed last.

**Method is the claim level.** The anchor this publishes says `VisualFix` and a
covariance of about +/-0.70 m at worst. That is what a reader inherits.
Publishing does not turn a visual fix into surveyed truth, and the record's own
`method`, `cov` and `confidence` are the only basis it offers. Per the
revision's binding paragraph, content named here inherits the anchor's frame
and precision -- which is exactly why the claim level matters.

    export SPATIALDDS_ENDPOINT=http://127.0.0.1:8787
    export SPATIALDDS_TOKEN=tok
    python3 examples/publish_anchor.py
"""
import json, os, pathlib, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit("  set SPATIALDDS_TOKEN: the surface is operator-scoped; the gateway's banner prints the export line"))}
d = json.loads((pathlib.Path(__file__).parents[1] / "samples/duck.input.json").read_text())
man, anc = d["manifest"], d["anchor"]
coll = BASE + anc["path"]
try:
    m = requests.put(BASE + "/spatialdds/resolve", json=man, headers=AUTH, timeout=10)
    a = requests.put(f'{coll}/{anc["key"].replace("/", "%2F")}', json=anc["record"], headers=AUTH, timeout=10)
except requests.RequestException as e:
    sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 --public-url https://demo.spatialdds.org --token tok --publish")
{m.status_code, a.status_code} <= {200, 201} or sys.exit(f"  refused: manifest {m.status_code}, anchor {a.status_code}. 405 means no --publish, 401 a wrong token")
print(f'  PUT manifest  -> {m.status_code}  {man["id"]}')
print(f'  PUT anchor    -> {a.status_code}  {anc["key"]}  method {anc["record"]["method"]}')
print("  GET anchors   -> " + ", ".join(sorted(x["anchor_id"] for x in requests.get(coll, timeout=10).json())))
ref = requests.get(BASE + "/spatialdds/resolve", params={"uri": anc["record"]["manifest_uri"]}, timeout=10).json()
print(f'  GET resolve   -> {ref["assets"][0]["media_type"]} {ref["assets"][0]["uri"]}')
print("\n" + json.dumps(a.json(), indent=2))   # the anchor itself, as served
