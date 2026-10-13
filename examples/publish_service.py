"""Publish a service announce, then find it through the read path.

The SSD successor in the write direction: `examples/migration/ssd_search.py`
reads a registry, this one writes to it. Together they are the round trip OSCP
had -- register a Spatial Service Record, then discover it.

The write surface is an implementation-defined extension, off unless the
endpoint was started with --publish, and operator-scoped when on. Token from
the environment; it is never put in a URL.

    SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 SPATIALDDS_TOKEN=... \\
        python3 examples/publish_service.py
"""
import json, os, pathlib, requests, sys
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit(
    "set SPATIALDDS_TOKEN: the publish surface is operator-scoped"))}
rec = json.loads((pathlib.Path(__file__).parents[1] /
                  "samples/published-records.input.json").read_text())["service"]
url = f'{BASE}{rec["path"]}/{rec["key"]}'
r = requests.put(url, json=rec["record"], headers=AUTH)
r.status_code in (200, 201) or sys.exit(f'  {url} refused the publish: '
    f'{r.status_code} {r.json().get("detail", "")}')
print(f'  PUT {rec["key"]:12s} -> {r.status_code} {"created" if r.status_code == 201 else "replaced"}')
print(f'  ETag {r.headers["ETag"]}')
found = [s["service"]["service_id"] for s in
         requests.get(f"{BASE}/.well-known/spatialdds/search").json()["results"]]
print(f'  search now returns: {", ".join(sorted(found))}')
