"""OSCP Spatial Content Discovery, and what replaces it.

Predecessor: OSCP SCD, `GET /scrs?h3Index=<cell>`, returning Spatial Content
Records -- a GeoPose plus content refs. The successor is `ContentAnnounce` with
a `manifest_uri`, resolved per §7.5 to a content manifest whose envelope carries
the assets. Content is positioned by its own pose and coverage, exactly as an
SCR was.

**The spatialdds:// hop here is a fixture, not a convention.** This endpoint
publishes no resolver, so the URI is mapped to a committed manifest by its last
path segment. The resolution mechanism is a pending design decision (see
docs/RESOLUTION.md); nothing in this mapping is a rule a client may infer.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/migration/scd_content.py
"""
import hashlib, os, pathlib, requests
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
svc = requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"][0]
topic = next(t for t in svc["service"]["topics"] if t["type"] == "content_announce")
for ann in requests.get(topic["url"]).json():
    man = __import__("json").loads((ROOT / "samples" / "manifests" /
          f'content-{ann["manifest_uri"].split("/")[-1]}.json').read_text())
    a = man["assets"][0]
    local = ROOT / "samples" / "assets" / a["uri"].split("/")[-1]
    got = "sha256:" + hashlib.sha256(local.read_bytes()).hexdigest()
    print(f'  {ann["title"]}\n    {ann["manifest_uri"]}\n    {a["media_type"]:18s} '
          f'{a["uri"]}\n    hash matches committed bytes: {got == a["hash"]}')
