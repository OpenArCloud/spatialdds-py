#!/usr/bin/env python3
"""The client's credential stays home, and it never guesses a scene.

Two findings, both in the client half, both found by using it against the live
endpoint rather than by any gate:

* **45.** `fetch_asset` follows an asset's absolute URL with the session that
  carries the endpoint's bearer token, so the token went to whoever hosts the
  asset. Every asset any gate fetched was served by the endpoint itself, where
  the token is welcome -- which is why no gate saw it. This one serves the
  asset from a *second* origin that records every header it receives, fetches
  it with a token-bearing client, and asserts the header never arrived there
  while it did arrive at the endpoint.
* **46.** With no scene configured, the publish verbs wrote into `default`,
  minting a lane nobody asked for. They now refuse, naming the scenes the
  endpoint serves.

    gates/client_scope.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

from aiohttp import web                                        # noqa: E402

from harness import FOUNTAIN, MANIFESTS, TOKEN, Gateway, report, state  # noqa: E402

import spatialdds as sd                                        # noqa: E402

AUTH = "demo.spatialdds.org"
SCENE = "ut-austin-south-mall"
ASSET = b'{"asset": "served from somewhere that is not the endpoint"}\n'


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    seen: list[dict] = []

    async def asset(request: web.Request) -> web.Response:
        seen.append(dict(request.headers))
        return web.Response(body=ASSET, content_type="model/gltf+json")

    host = web.Application()
    host.add_routes([web.get("/elsewhere.gltf", asset)])
    runner = web.AppRunner(host)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    other = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    gw = await Gateway(state(FOUNTAIN, MANIFESTS), advertise=f"https://{AUTH}",
                       publish=True).start()
    try:
        uri = f"spatialdds://{AUTH}/{SCENE}/content/elsewhere"
        manifest = {
            "id": uri, "profile": "spatial.manifest/1.8", "rtype": "content",
            "content": {"content_id": "content/elsewhere", "title": "Elsewhere",
                        "summary": "", "class_id": "", "tags": []},
            "assets": [{"uri": f"{other}/elsewhere.gltf",
                        "media_type": "model/gltf+json",
                        "hash": "sha256:" + hashlib.sha256(ASSET).hexdigest()}],
        }

        def run():
            ep = sd.connect(gw.base, token=TOKEN, scene=SCENE)
            w = ep.publish_manifest(manifest)
            raw = ep.resolve(uri).fetch_asset()
            return w, raw

        w, raw = await asyncio.to_thread(run)
        checks.append(("a token-bearing client publishes, resolves and "
                       "fetches an asset hosted on another origin",
                       w.created and raw == ASSET, f"{len(raw)} bytes"))
        checks.append(("the asset's host was asked exactly once",
                       len(seen) == 1, f"{len(seen)} request(s)"))
        leaked = [h for h in seen if "Authorization" in h
                  or "authorization" in h]
        checks.append(("no Authorization header reached the asset's host "
                       "(finding 45)", not leaked,
                       f"{[h.get('Authorization') for h in leaked]}"))
        # The other half: the token still goes where it belongs, or the check
        # above would pass for a client that had stopped sending it at all.
        ep = sd.connect(gw.base, token=TOKEN, scene=SCENE)
        checks.append(("the same client still sends its token to its own "
                       "endpoint",
                       ep._headers(url=gw.base + "/x").get("Authorization")
                       == f"Bearer {TOKEN}", ""))

        def unscened():
            ep = sd.connect(gw.base, token=TOKEN)
            try:
                ep.publish_content("stray", title="Stray",
                                   position=(30.285, -97.739),
                                   asset="https://cdn.example/x.gltf")
            except ValueError as e:
                return str(e)
            return None

        msg = await asyncio.to_thread(unscened)
        checks.append(("a publish with no scene configured refuses, naming "
                       "the scenes the endpoint serves (finding 46)",
                       msg is not None and SCENE in msg, f"{msg!r}"))
        st = gw.state
        checks.append(("and nothing was written: no `default` scene exists",
                       "default" not in st.scenes, f"{st.scenes}"))
    finally:
        await gw.stop()
        await runner.cleanup()
    return report("client_scope", checks,
                  "the client's token goes only to its endpoint, and a "
                  "publish never guesses a scene")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
