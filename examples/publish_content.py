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
BASE = os.environ.get("SPATIALDDS_ENDPOINT", "https://demo.spatialdds.org")
AUTH = {"Authorization": "Bearer " + (os.environ.get("SPATIALDDS_TOKEN") or sys.exit(
    "set SPATIALDDS_TOKEN: the publish surface is operator-scoped"))}
doc = json.loads((pathlib.Path(__file__).parents[1] / "samples/published-records.input.json").read_text())
con, man = doc["content"], doc["manifest"]
a = requests.put(f'{BASE}{con["path"]}/{con["key"].replace("/", "%2F")}', json=con["record"], headers=AUTH)
m = requests.put(f"{BASE}/spatialdds/resolve", json=man, headers=AUTH)
{a.status_code, m.status_code} <= {200, 201} or sys.exit(
    f'  refused: announce {a.status_code}, manifest {m.status_code}: {a.json().get("detail", "")}')
print(f'  announce -> {a.status_code}   manifest -> {m.status_code}')
back = requests.get(f"{BASE}/spatialdds/resolve", params={"uri": man["id"]})
print(f'  resolved {back.json()["id"]}\n    ttl_sec {back.json()["ttl_sec"]} '
      f'-> {back.headers["Cache-Control"]}')
