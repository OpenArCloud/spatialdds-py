"""OSCP Spatial Content Discovery, and what replaces it.

Predecessor: OSCP SCD, `GET /scrs?h3Index=<cell>`, returning Spatial Content
Records -- a GeoPose plus content refs. The successor is `ContentAnnounce` with
a `manifest_uri`, resolved per §7.5 to a content manifest whose envelope
carries the assets. Content is positioned by its own pose and coverage, exactly
as an SCR was.

The resolution is real: §7.5.1 says read the authority's resolver metadata and
follow the `https_base` it advertises, and that is what happens below. Nothing
is constructed from a path segment.

The chain ends in an asset reference and its declared digest, which is where a
client's job ends too. Fetching those bytes and comparing them is
`gates/content_resolution.py --remote`, because it needs the network and a
checkout and an example should need neither.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 python3 examples/migration/scd_content.py
"""
import os, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT") or "https://demo.spatialdds.org"
# Against the authority itself every advertised URL is followed as given. A
# local copy of it answers to a different name, so only the path is reused --
# the one substitution a reader running their own endpoint needs, and one a
# real client never does.
at = lambda u: f"{BASE}/{u.split('/', 3)[3]}"
try: hits = requests.get(f"{BASE}/.well-known/spatialdds/search", timeout=10).json()["results"]
except requests.RequestException as e: sys.exit(f"  {BASE} is not answering ({type(e).__name__}). Serve it: python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787 --public-url https://demo.spatialdds.org")
topics = [t for r in hits for t in r["service"]["topics"]]
topic = next((t for t in topics if t["type"] == "content_announce"), None) or sys.exit(
    f'{BASE} serves no content_announce topic; it offers: {", ".join(sorted({t["type"] for t in topics}))}')
meta = requests.get(f"{BASE}/.well-known/spatialdds/resolver", timeout=10).json()
resolve = at(meta.get("https_base") or sys.exit(f"  {BASE} is not an authority, so nothing resolves; serve it as one with --public-url https://demo.spatialdds.org"))
for ann in requests.get(at(topic["url"])).json():
    man = requests.get(resolve, params={"uri": ann["manifest_uri"]}).json()
    a = man["assets"][0]
    print(f'  {man["content"]["title"]}\n    resolved {man["id"]}\n'
          f'    {a["media_type"]:18s} {a["uri"]}\n    {a["hash"]}')
