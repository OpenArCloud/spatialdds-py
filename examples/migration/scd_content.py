"""OSCP Spatial Content Discovery, and what replaces it.

Predecessor: OSCP SCD, `GET /scrs?h3Index=<cell>`, returning Spatial Content
Records -- a GeoPose plus content refs. The successor is `ContentAnnounce` with
a `manifest_uri`, resolved per §7.5 to a content manifest whose envelope
carries the assets. Content is positioned by its own pose and coverage, exactly
as an SCR was.

The resolution is real: §7.5.1 says read the authority's resolver metadata and
follow the `https_base` it advertises, and that is what happens below. Nothing
is constructed from a path segment.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/migration/scd_content.py
"""
import hashlib, os, pathlib, requests
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
# Against the authority itself every advertised URL is followed as given. A
# local copy of it answers to a different name, so only the path is reused --
# the one substitution a reader running their own endpoint needs, and one a
# real client never does.
at = lambda u: f"{BASE}/{u.split('/', 3)[3]}"
svc = requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"][0]
topic = next(t for t in svc["service"]["topics"] if t["type"] == "content_announce")
resolve = at(requests.get(f"{BASE}/.well-known/spatialdds/resolver").json()["https_base"])
for ann in requests.get(at(topic["url"])).json():
    man = requests.get(resolve, params={"uri": ann["manifest_uri"]}).json()
    a = man["assets"][0]
    local = ROOT / "samples" / "assets" / a["uri"].split("/")[-1]
    got = "sha256:" + hashlib.sha256(local.read_bytes()).hexdigest()
    print(f'  {man["content"]["title"]}\n    resolved {man["id"]}\n'
          f'    {a["media_type"]:18s} {a["uri"]}\n'
          f'    hash matches committed bytes: {got == a["hash"]}')
