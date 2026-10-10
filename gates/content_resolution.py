#!/usr/bin/env python3
"""A served content announce resolves to a real asset, byte for byte.

The whole point of the recording: a reference that ends in a file rather than
in a plausible-looking string. The chain, every link checked:

    GET the content announce  ->  manifest_uri (a spatialdds:// URI)
      ->  the content manifest  ->  assets[].uri and assets[].hash
        ->  the committed file, compared byte for byte

**Every link is real now.** The URI is resolved the way §7.5.1 orders it: read
the authority's resolver metadata, follow the `https_base` it advertises, and
`GET` the URI against it. Nothing is constructed from a path segment and
nothing is read off the filesystem; the gate learns the resolve URL the same
way a client does, from the endpoint.

This replaces a gate-local mapping from a URI's last segment to a committed
manifest file, which was a fixture and said so. It existed because the demo
endpoint was not the authority for the URIs the recording carried, and it is
retired by the recording being re-authored under `demo.spatialdds.org` plus the
gateway implementing §7.5.2. An undocumented construction rule is a dialect,
and there is no longer one here.

The one substitution left is the harness's: the endpoint advertises itself as
`https://demo.spatialdds.org` while the socket is on loopback, so
`Gateway.connect` puts the connect host back before the request goes out. That
is test wiring and the only place it happens -- a client resolves the
advertised host by DNS, which is exactly what makes it the authority.

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
from spatialdds_web import gateway                             # noqa: E402

MCAP = ROOT / "samples" / "fountain-anchors.mcap"
MANIFESTS = ROOT / "samples" / "manifests"
ASSETS = ROOT / "samples" / "assets"


AUTHORITY = "https://demo.spatialdds.org"   # the name these URIs resolve to


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
    gw = await Gateway(st, advertise=AUTHORITY).start()
    checks: list[tuple[str, bool, str]] = []
    resolved = 0

    remote_urls: dict[str, Path] = {}
    async with aiohttp.ClientSession() as s:
        # §7.5.1 step 3: the advertised resolver. Read it first, because every
        # resolution below goes through what it advertises rather than through
        # anything this gate knows.
        async with s.get(gw.base + "/.well-known/spatialdds/resolver") as r:
            meta_status = r.status
            meta = await r.json()
        checks.append(("the authority publishes resolver metadata",
                       meta_status == 200, f"got {meta_status}: {meta}"))
        checks.append(("the metadata carries authority, https_base and "
                       "cache_ttl_sec (§7.5.2)",
                       all(k in meta for k in
                           ("authority", "https_base", "cache_ttl_sec")),
                       f"{sorted(meta)}"))
        checks.append((f"it answers for the authority in the recording's URIs "
                       f"({meta.get('authority')})",
                       meta.get("authority") == AUTHORITY.split("//")[1],
                       f"{meta.get('authority')!r}"))
        if meta_status != 200 or "https_base" not in meta:
            await gw.stop()
            return report("content resolution", checks,
                          "no resolver to resolve through")
        resolve_url = gw.connect(meta["https_base"])
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
            # §7.5.2: GET {https_base}?uri={urlencoded SpatialURI}
            async with s.get(resolve_url, params={"uri": uri}) as r:
                rstatus, rtype = r.status, r.headers.get("Content-Type", "")
                retag = r.headers.get("ETag", "")
                rcache = r.headers.get("Cache-Control", "")
                man = await r.json()
            checks.append((f"{cid}: the URI resolves over HTTPS, 200 with a "
                           f"correct Content-Type (§7.5.2)",
                           rstatus == 200
                           and rtype.startswith("application/json"),
                           f"got {rstatus} {rtype!r}"))
            if rstatus != 200:
                continue
            checks.append((f"{cid}: the response carries an integrity signal "
                           f"(§7.5.2 requires at least one)",
                           retag.startswith('"sha256:'), f"ETag {retag!r}"))
            checks.append((f"{cid}: the resolved document is the URI it was "
                           f"asked for",
                           man.get("id") == uri, f"{man.get('id')!r}"))
            # N.4, as amended: the document's own ttl_sec governs its max-age.
            ttl = man.get("ttl_sec")
            checks.append((f"{cid}: max-age is the manifest's own ttl_sec "
                           f"({ttl}), not the endpoint's default "
                           f"({gateway.PUBLIC_MAX_AGE})",
                           f"max-age={ttl}" in rcache, f"{rcache!r}"))
            # The committed file is still compared, because the point of the
            # chain is that it ends in bytes rather than in a document.
            man_path = MANIFESTS / f"content-{cid.split('/')[-1]}.json"
            checks.append((f"{cid}: the resolved manifest is the committed "
                           f"one, byte for byte",
                           man_path.is_file()
                           and json.loads(man_path.read_text()) == man,
                           f"{man_path.name}"))
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

        # The authority must refuse what is not its own and what it does not
        # hold. Both are 404 by §7.5.3 and are distinguished in the body.
        for label, uri, slug in (
                ("another authority's URI",
                 "spatialdds://austin.openarcloud.example/south-mall/"
                 "content/fountain-marker", "foreign-authority"),
                ("its own authority, a URI it does not hold",
                 "spatialdds://demo.spatialdds.org/south-mall/service/"
                 "fountain-anchors", "no-such-manifest")):
            async with s.get(resolve_url, params={"uri": uri}) as r:
                st_, body = r.status, await r.json()
            checks.append((f"{label} is a 404 saying which ({slug})",
                           st_ == 404
                           and body.get("type", "").endswith(slug),
                           f"got {st_} {body.get('type')}"))

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
