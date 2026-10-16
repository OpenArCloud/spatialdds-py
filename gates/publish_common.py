"""Shared plumbing for the publish gates. Not a gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
# The gateway lives in the distribution under lib/spatialdds; a gate runs
# from a checkout, so the checkout's copy goes on the path rather than an
# installed one.
sys.path.insert(0, str(ROOT / "lib" / "spatialdds"))

import aiohttp                                                 # noqa: E402

from harness import FOUNTAIN, MANIFESTS, TOKEN, state          # noqa: E402

INPUT = ROOT / "samples" / "published-records.input.json"


def authored() -> dict:
    """The authored input every expected value comes from."""
    return json.loads(INPUT.read_text())


def authority_base(doc: dict) -> str:
    return f"https://{doc['authority']}"


def seeded(doc: dict):
    """A state seeded from the fountain recording, as the endpoint serves it."""
    return state(FOUNTAIN, MANIFESTS)


async def put(s: aiohttp.ClientSession, base: str, path: str, body,
              token: str | None = TOKEN, headers: dict | None = None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    async with s.put(base + path, json=body, headers=h) as r:
        return r.status, r.headers, await r.read()


async def delete(s: aiohttp.ClientSession, base: str, path: str,
                 token: str | None = TOKEN):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    async with s.delete(base + path, headers=h) as r:
        return r.status, r.headers, await r.read()


async def get(s: aiohttp.ClientSession, base: str, path: str,
              token: str | None = None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    async with s.get(base + path, headers=h) as r:
        return r.status, r.headers, await r.read()


def resolve_path(uri: str) -> str:
    from spatialdds.server import gateway
    return f"{gateway.RESOLVE_PATH}?uri={quote(uri, safe='')}"
