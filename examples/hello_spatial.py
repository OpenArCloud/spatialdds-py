"""Hello spatial: discover a SpatialDDS service and read typed samples.

Ten lines, `requests` and `websockets`, no DDS and no code from this
repository. Start an endpoint first (see examples/README.md), then run this.

The shape is the point. Line 3 asks the discovery endpoint what is here and
gets a service manifest; line 4 picks a topic out of it; the rest subscribes to
that topic's own URL and reads typed zones until the server says the latched
set is complete. Only the base URL is hardcoded: the path is followed, not
constructed.
"""
import asyncio, json, os, requests, websockets
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "http://127.0.0.1:8787")
svc = requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"][0]
topic = next(t for t in svc["service"]["topics"] if t["type"] == "spatial_zone")
async def main():
    async with websockets.connect(topic["url"].replace("http://", "ws://")) as ws:
        while (e := json.loads(await ws.recv()))["event"] != "end_of_replay":
            print(f'{e["data"]["kind"]:16s} {e["data"]["name"]:20s} {e["key"]}')
        print(f'latched set complete: {e["replayed"]} zone(s)')
asyncio.run(main())
