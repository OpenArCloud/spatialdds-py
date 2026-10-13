#!/usr/bin/env python3
"""One service announce and one content record, published and checked whole.

Every expected value comes from `samples/published-records.input.json`, which
is authored data, and never from the gateway. Asking the endpoint what it
stored and then checking that it serves that would pass for any endpoint,
including a broken one.

The second half is the property the write surface exists to have: a published
instance is indistinguishable from a replayed one. So the served record is put
through the same checks a recorded instance passes -- schema-valid against the
vendored schema, ETag equal to the digest of its own canonical form, present in
the latched set, found by search, and byte-identical to the authored input.
If any of those held for replayed instances and not for published ones, the
surface would be an exception to the binding rather than an extension of it.

Usage:  gates/golden_publish.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from publish_common import (ROOT, aiohttp, authored, authority_base, get,  # noqa: E402
                            put, resolve_path, seeded)
from harness import Gateway, report                            # noqa: E402
from spatialdds_web import gateway, webjson                    # noqa: E402

import jsonschema                                              # noqa: E402


async def main() -> int:
    doc = authored()
    base = authority_base(doc)
    gw = await Gateway(seeded(doc), advertise=base, publish=True).start()
    checks: list[tuple[str, bool, str]] = []
    schemas = webjson.schemas()

    async with aiohttp.ClientSession() as s:
        for kind in ("service", "content"):
            spec = doc[kind]
            url = f"{spec['path']}/{spec['key'].replace('/', '%2F')}"
            want = spec["record"]
            st_, h, body = await put(s, gw.base, url, want)
            got = json.loads(body) if body else {}
            checks.append((f"{kind}: PUT creates it (201)", st_ == 201,
                           f"got {st_}: {body[:160]!r}"))
            if st_ != 201:
                continue
            checks.append((f"{kind}: the response is the served canonical "
                           f"form, field for field against the input",
                           got == want,
                           f"differs at "
                           f"{sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))}"))
            checks.append((f"{kind}: the response carries the instance ETag, "
                           f"which is the digest of its canonical form",
                           h.get("ETag") == f'"sha256:{webjson.digest(want)}"',
                           f"{h.get('ETag')!r}"))
            checks.append((f"{kind}: a write is operator-scoped, so no-store",
                           "no-store" in h.get("Cache-Control", ""),
                           f"{h.get('Cache-Control')!r}"))
            # Now read it back the way any reader would.
            st2, h2, body2 = await get(s, gw.base, url)
            served = json.loads(body2)
            checks.append((f"{kind}: GET on the same URL serves it (200)",
                           st2 == 200, f"got {st2}"))
            checks.append((f"{kind}: what a reader gets equals the authored "
                           f"input", served == want, "differs"))
            checks.append((f"{kind}: its ETag over GET is the same digest",
                           h2.get("ETag") == f'"sha256:{webjson.digest(want)}"',
                           f"{h2.get('ETag')!r}"))
            checks.append((f"{kind}: cacheable on the read side, unchanged by "
                           f"having been published",
                           "max-age" in h2.get("Cache-Control", ""),
                           f"{h2.get('Cache-Control')!r}"))
            try:
                jsonschema.validate(served, schemas[spec["fqn"]])
                ok, why = True, ""
            except jsonschema.ValidationError as e:
                ok, why = False, e.message[:120]
            checks.append((f"{kind}: validates against the vendored "
                           f"{spec['fqn']} schema", ok, why))
            st3, _, body3 = await get(s, gw.base, spec["path"])
            coll = json.loads(body3)
            checks.append((f"{kind}: it is in the latched collection",
                           any(i == want for i in coll),
                           f"{len(coll)} instance(s), none equal"))

        # Canonicalization, observably. Every other record here is already
        # canonical, so storing a body as it arrived would change nothing a
        # check could see -- which a fault injection proved, catching zero
        # failures. This record is schema-valid and not canonical, and what
        # comes back must be the canonical form.
        probe = doc["canonical_probe"]
        purl = f"{probe['path']}/{probe['key'].replace('/', '%2F')}"
        stp, hp, bp = await put(s, gw.base, purl, probe["sent"])
        back = json.loads(bp) if bp else {}
        checks.append(("a schema-valid body carrying undeclared members is "
                       "accepted (201)",
                       stp == 201, f"got {stp}: {bp[:160]!r}"))
        as_text = webjson.canon
        checks.append(("what comes back is the canonical form, with the "
                       "members the type does not declare dropped",
                       as_text(back) == as_text(probe["canonical"])
                       and as_text(back) != as_text(probe["sent"]),
                       f"sent {as_text(probe['sent'])[:90]}... vs got "
                       f"{as_text(back)[:90]}..."))
        # Byte-level, against the wire serialisation rather than against
        # `canon`: MAPPING.md requires canonical form for digest input, not
        # for the body, and the served body is sorted JSON whose floats keep
        # their trailing `.0`. The digest is checked separately, above.
        _st, _h, bq = await get(s, gw.base, purl)
        wire = json.dumps(probe["canonical"], sort_keys=True,
                          separators=(",", ":")).encode()
        checks.append(("and a reader gets that same record, byte for byte",
                       bq == wire,
                       f"{len(bq)} vs {len(wire)} bytes"))

        # The published service must be findable, which is what makes it a
        # registry entry rather than a stored document -- and the recorded one
        # must still be there beside it. That second half is the regression
        # gate for a read-side defect only a writer could expose: search
        # returned one manifest per *scene*, picking the first announce, so a
        # second announce into a scene the recording already described was
        # served perfectly over GET and invisible to search. Asserting only
        # that the published service appears would pass a fix that displaced
        # the recorded one instead of joining it.
        _, _, b = await get(s, gw.base, "/.well-known/spatialdds/search")
        results = json.loads(b)["results"]
        ids = sorted(r["service"]["service_id"] for r in results)
        published = doc["service"]["record"]["service_id"]
        recorded = "fountain-anchors"          # the announce the seed carries
        scenes = {r["service"]["service_id"]:
                  sorted({t["name"].split("/")[1] for t in
                          r["service"]["topics"]}) for r in results}
        checks.append((f"two announces in one scene both appear in search, "
                       f"the published one and the recorded one ({ids})",
                       ids == sorted([published, recorded]), f"{ids}"))
        checks.append((f"and they are the same scene, so this exercises the "
                       f"one-manifest-per-scene path "
                       f"({sorted(set(sum(scenes.values(), [])))})",
                       len({tuple(v) for v in scenes.values()}) == 1
                       and len(scenes) == 2, f"{scenes}"))
        geo = doc.get("geohash", "9v6krc")
        _, _, bg = await get(
            s, gw.base, f"/.well-known/spatialdds/search?geohash={geo}")
        gids = sorted(r["service"]["service_id"]
                      for r in json.loads(bg)["results"])
        checks.append((f"and a geohash query over that scene returns both "
                       f"({gids})", gids == sorted([published, recorded]),
                       f"{gids}"))

        # Gate 5: a published manifest resolves end to end, with the §7.5.2
        # and N.4 rules unchanged -- ttl from the document, ETag a digest.
        man = doc["manifest"]
        stm, _, _ = await put(s, gw.base, gateway.RESOLVE_PATH, man)
        checks.append(("the content manifest is accepted (201)", stm == 201,
                       f"got {stm}"))
        stn, hn, bn = await get(s, gw.base, resolve_path(man["id"]))
        back = json.loads(bn) if bn else {}
        checks.append(("it resolves through the resolver route (200)",
                       stn == 200, f"got {stn}"))
        checks.append(("the resolved document is the authored manifest",
                       back == man, "differs"))
        checks.append((f"its max-age is the manifest's own ttl_sec "
                       f"({man['ttl_sec']}), not the endpoint's default "
                       f"({gateway.PUBLIC_MAX_AGE})",
                       f"max-age={man['ttl_sec']}" in hn.get("Cache-Control", ""),
                       f"{hn.get('Cache-Control')!r}"))
        checks.append(("its ETag is the digest of its canonical form",
                       hn.get("ETag") == f'"sha256:{webjson.digest(man)}"',
                       f"{hn.get('ETag')!r}"))

    await gw.stop()
    return report("golden publish", checks,
                  "a published record is indistinguishable from a replayed one")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
