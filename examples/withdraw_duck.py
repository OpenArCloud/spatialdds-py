"""Withdraw the duck, and watch the withdrawal arrive.

The other half of publish_duck.py. Open the content lane's WebSocket first and
read the latched set to `end_of_replay`, so the duck is seen there; then DELETE
its announce with a reason, and read on until the dispose event brings that
reason back. A subscriber learns of a withdrawal from the stream, not by
polling, which is why the socket is open before the DELETE is sent.

The last lines are the read path afterwards: the announce gone, the collection
back to the fountain's own two records -- and the duck's manifest still
resolving, because this surface has no route that withdraws a manifest
(WEB-BINDING-FINDINGS.md, finding 44). Only a deliberate reset removes it.

Run publish_duck.py first; against an endpoint with no duck this says so.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 SPATIALDDS_TOKEN=tok \\
        python3 examples/withdraw_duck.py
"""
import json, os, pathlib, requests, sys, websockets.sync.client as wsc
from websockets.exceptions import WebSocketException
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit("  set SPATIALDDS_TOKEN: the surface is operator-scoped; the gateway's banner prints the export line"))}
d = json.loads((pathlib.Path(__file__).parents[1] / "samples/duck.input.json").read_text())
ann, reason = d["announce"], "walkthrough: the duck is withdrawn"
coll, key = BASE + ann["path"], ann["key"].replace("/", "%2F")
try: ws = wsc.connect(coll.replace("http", "ws", 1), open_timeout=10)
except (OSError, WebSocketException) as e: sys.exit(f"  {BASE} has no content lane to watch ({type(e).__name__}). Serve the fountain set with --publish, then run examples/publish_duck.py")
with ws:
    replay = []
    while (e := json.loads(ws.recv(timeout=10)))["event"] != "end_of_replay": replay.append(e["key"])
    print("  WS replay     -> " + ", ".join(sorted(replay)))
    ann["key"] in replay or sys.exit(f"  no duck at {BASE} to withdraw: publish it first, python3 examples/publish_duck.py")
    r = requests.delete(f"{coll}/{key}", params={"reason": reason}, headers=AUTH, timeout=10)
    r.status_code == 204 or sys.exit(f"  refused: DELETE {r.status_code}. 405 means no --publish, 401 a wrong token")
    print(f'  DELETE        -> {r.status_code}  {ann["key"]}  reason="{reason}"')
    while (e := json.loads(raw := ws.recv(timeout=10)))["event"] != "dispose": pass
print("  WS dispose    -> " + raw)
print(f'  GET announce  -> {requests.get(f"{coll}/{key}", timeout=10).status_code}')
print("  GET list      -> " + ", ".join(sorted(c["content_id"] for c in requests.get(coll, timeout=10).json())))
print(f'  GET resolve   -> {requests.get(BASE + "/spatialdds/resolve", params={"uri": d["manifest"]["id"]}, timeout=10).status_code}  the manifest stays: no route withdraws one')
