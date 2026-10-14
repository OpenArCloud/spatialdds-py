"""Write to bytes in one file: publish a duck, then fetch the duck.

The one example that runs the whole chain. PUT the content manifest, PUT the
announce that places it, then back out through the read path a reader uses:
the content collection showing the duck beside the fountain's own content, the
`spatialdds://` URI resolved, and the asset's ref fetched so the last line is
a byte count rather than a promise.

The duck is placed by its announce's own position and coverage. No anchor
references it, because no 1.8 anchor type can carry a content reference -- see
docs/WALKTHROUGH.md. That absence is the exhibit, not an omission.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 SPATIALDDS_TOKEN=tok \\
        python3 examples/publish_duck.py
"""
import hashlib, json, os, pathlib, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit(
    "set SPATIALDDS_TOKEN: the publish surface is operator-scoped"))}
d = json.loads((pathlib.Path(__file__).parents[1] / "samples/duck.input.json").read_text())
man, ann = d["manifest"], d["announce"]
coll, key = BASE + ann["path"], ann["key"].replace("/", "%2F")
m = requests.put(BASE + "/spatialdds/resolve", json=man, headers=AUTH)
a = requests.put(f"{coll}/{key}", json=ann["record"], headers=AUTH)
{m.status_code, a.status_code} <= {200, 201} or sys.exit(f"  refused: {m.status_code}/{a.status_code}")
print(f"  manifest -> {m.status_code}   announce -> {a.status_code}")
print("  content here: " + ", ".join(sorted(c["content_id"] for c in requests.get(coll).json())))
ref = requests.get(BASE + "/spatialdds/resolve", params={"uri": man["id"]}).json()["assets"][0]
raw = requests.get(BASE + "/" + ref["uri"].split("/", 3)[3]).content
print(f'  resolved {man["id"]}\n  fetched {ref["media_type"]} {len(raw)} bytes, hash matches: {hashlib.sha256(raw).hexdigest() == ref["hash"].split(":")[1]}')
