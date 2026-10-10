#!/usr/bin/env python3
"""A served content announce resolves to a real asset, byte for byte.

The whole point of the recording: a reference that ends in a file rather than
in a plausible-looking string. The chain, every link checked:

    GET the content announce  ->  manifest_uri (a spatialdds:// URI)
      ->  the content manifest  ->  assets[].uri and assets[].hash
        ->  the committed file, compared byte for byte

**One link is a test fixture and says so.** §7.5 resolution is "check cache,
try an advertised resolver, fall back to HTTPS", and this endpoint publishes no
resolver — it returns 404 from the resolver path, which Appendix N.2 makes
conformant for a gateway with no DDS domain. So the URI is resolved here by a
gate-local mapping from its last path segment to a committed manifest file.
That mapping is a fixture, not a wire convention, and is not a rule any client
should infer: an undocumented construction rule is a dialect.

**The remote form is unverified, deliberately.** `assets[].uri` is a
`raw.githubusercontent.com` URL that only exists once the commit is pushed, so
this gate byte-compares against the committed file and checks that the URL's
shape and tail agree with it. Fetching the remote is a post-push check, noted
rather than skipped silently.

Usage:  gates/content_resolution.py
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402

from harness import Gateway, report, state                     # noqa: E402

MCAP = ROOT / "samples" / "fountain-anchors.mcap"
MANIFESTS = ROOT / "samples" / "manifests"
ASSETS = ROOT / "samples" / "assets"


def resolve_fixture(uri: str) -> Path:
    """The gate-local stand-in for §7.5 resolution. A fixture, not a rule."""
    return MANIFESTS / f"content-{uri.rstrip('/').split('/')[-1]}.json"


async def fetch_remote(urls: dict[str, Path]) -> int:
    """Fetch each asset's published URL and compare it to the committed file.

    Only meaningful after the commit is pushed, which is why it is a flag and
    not part of the default run: a gate that needs the network to pass is a
    gate that fails on a train.
    """
    checks: list[tuple[str, bool, str]] = []
    async with aiohttp.ClientSession() as s:
        for url, committed in sorted(urls.items()):
            want = committed.read_bytes()
            try:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    code, body = r.status, await r.read()
            except Exception as e:
                checks.append((f"{committed.name}: fetched from its published "
                               f"URL", False, f"{type(e).__name__}: {e}"))
                continue
            checks.append((f"{committed.name}: fetched 200 from its published "
                           f"URL ({len(body)} bytes)", code == 200,
                           f"status {code}"))
            checks.append((f"{committed.name}: the published bytes are the "
                           f"committed bytes",
                           body == want,
                           f"remote sha256 "
                           f"{hashlib.sha256(body).hexdigest()[:16]}…, "
                           f"committed "
                           f"{hashlib.sha256(want).hexdigest()[:16]}…"))
    return report("remote asset resolution", checks,
                  "every published asset URL serves the committed bytes")


async def run(remote: bool = False) -> int:
    st = state(MCAP)
    gw = await Gateway(st).start()
    checks: list[tuple[str, bool, str]] = []
    resolved = 0

    remote_urls: dict[str, Path] = {}
    async with aiohttp.ClientSession() as s:
        res = next((r for r in st.resources.values()
                    if r.fqn == "spatial.disco.ContentAnnounce"), None)
        if res is None:
            checks.append(("the recording carries content announces", False,
                           "none found"))
            await gw.stop()
            return report("content resolution", checks, "nothing to resolve")

        async with s.get(gw.base + res.path) as r:
            announces = await r.json()
        checks.append((f"{len(announces)} content announce(s) served",
                       len(announces) >= 2,
                       "the directive asks for at least two"))

        for ann in announces:
            cid = ann["content_id"]
            uri = ann["manifest_uri"]
            checks.append((f"{cid}: manifest_uri is a spatialdds:// URI",
                           isinstance(uri, str)
                           and uri.startswith("spatialdds://"), f"{uri!r}"))
            man_path = resolve_fixture(uri)
            checks.append((f"{cid}: the URI resolves to a committed manifest",
                           man_path.is_file(), f"{man_path}"))
            if not man_path.is_file():
                continue
            man = json.loads(man_path.read_text())
            checks.append((f"{cid}: the manifest is spatial.manifest/1.x, "
                           f"rtype content",
                           man.get("profile", "").startswith(
                               "spatial.manifest/1.")
                           and man.get("rtype") == "content",
                           f"{man.get('profile')} {man.get('rtype')}"))
            checks.append((f"{cid}: the manifest's content_id matches the "
                           f"announce",
                           man.get("content", {}).get("content_id") == cid,
                           f"{man.get('content', {}).get('content_id')}"))
            assets = man.get("assets") or []
            checks.append((f"{cid}: the manifest carries at least one asset "
                           f"with uri, media_type and hash",
                           bool(assets) and all(
                               k in assets[0]
                               for k in ("uri", "media_type", "hash")),
                           f"{assets[:1]}"))
            if not assets:
                continue
            a0 = assets[0]
            # The committed file the reference is about.
            committed = ASSETS / a0["uri"].rstrip("/").split("/")[-1]
            checks.append((f"{cid}: the asset's URL tail names a committed "
                           f"file ({committed.name})",
                           committed.is_file(), f"{committed}"))
            if not committed.is_file():
                continue
            raw = committed.read_bytes()
            want = a0["hash"]
            got = "sha256:" + hashlib.sha256(raw).hexdigest()
            checks.append((f"{cid}: the committed file is byte-for-byte what "
                           f"the manifest's hash declares ({len(raw)} bytes)",
                           got == want, f"file {got[:22]}…, manifest "
                                        f"{want[:22]}…"))
            checks.append((f"{cid}: the asset URL is an absolute https URL",
                           a0["uri"].startswith("https://"), f"{a0['uri']}"))
            checks.append((f"{cid}: media_type is stated "
                           f"({a0.get('media_type')})",
                           bool(a0.get("media_type")), ""))
            remote_urls[a0["uri"]] = committed
            resolved += 1

        # The content announce's coverage must sit on the anchor it describes,
        # which is the whole co-located model: content is positioned by its own
        # pose and coverage, not by a reference to an anchor.
        anchor_res = next(r for r in st.resources.values()
                          if r.fqn == "spatial.core.GeoAnchor")
        async with s.get(gw.base + anchor_res.path) as r:
            anchors = await r.json()
        pts = {(round(a["geopose"]["lat_deg"], 7),
                round(a["geopose"]["lon_deg"], 7)) for a in anchors}
        off = []
        for ann in announces:
            cov = (ann.get("coverage") or [{}])[0]
            if not cov.get("has_circle"):
                off.append(f"{ann['content_id']}: no circle")
                continue
            lon, lat, _alt = cov["circle_center"]
            if (round(lat, 7), round(lon, 7)) not in pts:
                off.append(f"{ann['content_id']}: circle centre is not an "
                           f"anchor position")
        checks.append(("every content announce carries a coverage circle "
                       "centred on one of the recording's anchor positions",
                       not off, f"{off[:2]}"))

    await gw.stop()
    checks.append((f"{resolved} reference chain(s) followed end to end",
                   resolved >= 2, f"{resolved}"))
    print(f"  note the asset URLs point at raw.githubusercontent.com and "
          f"resolve only after this commit is pushed; the byte comparison "
          f"above is against the committed file.\n")
    rc = report(f"content resolution {MCAP.name}", checks,
                "a served reference ends in a real file")
    if remote:
        print()
        rc = await fetch_remote(remote_urls) or rc
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", action="store_true",
                    help="also fetch each asset's published URL and compare "
                         "it to the committed file; only meaningful once the "
                         "commit is pushed")
    sys.exit(asyncio.run(run(ap.parse_args().remote)))
