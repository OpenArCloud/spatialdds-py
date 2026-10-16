#!/usr/bin/env python3
"""A registry that starts empty: where its types come from, and what persists.

James's ruling, and the reason this gate exists: there is no "required base
MCAP". A recording cosplaying as a schema file would haunt every fresh
registry forever. So the type source is the specification pin -- the IDL
vendored inside the distribution at `SPEC_COMMIT` -- and that choice has to be
checked rather than asserted, in four ways:

**The pin agrees with the tables beside it.** Two readings of one
specification: `idlmodel` parsing the vendored IDL, and the generated
serializer tables produced upstream by a different program. They have to agree
on every type's kind, field order, enum members and union cases. This is the
same argument `gates/wire_model.py` makes for a recording's embedded IDL, and
it is the argument that caught finding 33.

**The pin is the same text the committed recording carries.** `pin.flatten`
has to reproduce, byte for byte, the schema records in
`samples/fountain-anchors.mcap` -- which were written from a specification
checkout by `tools/author_anchors.py`. If it does not, a journal written by a
fresh registry and a recording of the same types would describe them
differently, and "a backup is a recording" would be true of only one of them.
The first version of `pin.flatten` joined its sections with `""` instead of
`"\\n"` and failed this by two bytes per record.

**The loop closes.** Serve empty, publish all three kinds and a manifest,
find them by search and by resolution, restart from the journal alone, and
get the same documents back. Expected values come from the committed
authoring inputs, never from the gateway's own answer.

**The absence is still a refusal.** With no usable IDL the gateway is
typeless, which is a legitimate read-only state, and switching publishing on
then says so in a sentence naming what it looked for.

Usage:  gates/fresh_start.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))
LIB = ROOT / "lib" / "spatialdds"
sys.path.insert(0, str(LIB))

import aiohttp                                                 # noqa: E402
from mcap.reader import make_reader                            # noqa: E402

from harness import FOUNTAIN, TOKEN, Gateway, report          # noqa: E402
from spatialdds.server import gateway, pin, webjson            # noqa: E402

PUB = ROOT / "samples" / "published-records.input.json"


def geohash(lat: float, lon: float, precision: int) -> str:
    """The cell a point falls in. Written out here rather than imported.

    The client library has `geohash_encode`, and using it would make this gate
    check the gateway's search against the client's arithmetic -- two of this
    repository's own components agreeing. Twelve lines of standard geohash
    keeps the expected value independent of both.
    """
    b32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    lo, hi, lo2, hi2 = -90.0, 90.0, -180.0, 180.0
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lo2 + hi2) / 2
            if lon > mid:
                ch = ch * 2 + 1
                lo2 = mid
            else:
                ch *= 2
                hi2 = mid
        else:
            mid = (lo + hi) / 2
            if lat > mid:
                ch = ch * 2 + 1
                lo = mid
            else:
                ch *= 2
                hi = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(b32[ch])
            bit, ch = 0, 0
    return "".join(out)
DUCK = ROOT / "samples" / "duck.input.json"
DEMO = "https://demo.spatialdds.org"


def pin_vs_tables(checks: list) -> None:
    model, idl = pin.wire_model()
    T = webjson.tables()
    common = sorted(set(model.aggregates) & set(T))
    kind_bad, order_bad, enum_bad, union_bad = [], [], [], []
    for fqn in common:
        agg, tbl = model.aggregates[fqn], T[fqn]
        if agg.kind != tbl.get("kind"):
            kind_bad.append(fqn)
            continue
        if agg.kind == "struct":
            mine = [m.name for m in agg.members]
            theirs = [n for n, _ in tbl["fields"]]
            if mine != theirs:
                order_bad.append(f"{fqn}: {mine} vs {theirs}")
        elif agg.kind == "enum" and agg.enumerators != tbl["members"]:
            enum_bad.append(fqn)
        elif agg.kind == "union" and \
                [l[0] for l, _ in agg.cases] != [c[0] for c in tbl["cases"]]:
            union_bad.append(fqn)
    checks.append((f"the vendored IDL yields {len(model.aggregates)} types, "
                   f"{len(common)} of them also in the generated tables",
                   len(common) >= 40, f"only {len(common)} matched"))
    checks.append(("every shared type has the same kind, members in the same "
                   "order, enum members and union cases in both readings",
                   not (kind_bad or order_bad or enum_bad or union_bad),
                   "; ".join((kind_bad + order_bad + enum_bad +
                              union_bad)[:3])))
    # Said out loud rather than left to look like full coverage: these three
    # closures are the publishable kinds' dependencies, not the whole 1.8
    # type system. A fourth publishable kind means a fourth closure and
    # probably another vendored file.
    uncovered = sorted(set(T) - set(model.aggregates))
    print(f"  note the pin covers {len(common)} of {len(T)} table entries; "
          f"{len(uncovered)} types are not in any closure (e.g. "
          f"{', '.join(q.split('.')[-1] for q in uncovered[:3])})")
    want = {"spatial.disco.Announce", "spatial.disco.ContentAnnounce",
            "spatial.core.GeoAnchor"}
    checks.append((f"all three publishable kinds are in the pin, each with "
                   "its @key -- which is why the IDL and not the tables is "
                   "the type source",
                   want <= set(model.aggregates)
                   and all(any(m.is_key for m in model.aggregates[w].members)
                           for w in want),
                   f"missing {sorted(want - set(model.aggregates))}"))
    checks.append((f"every publishable kind has an IDL record to journal "
                   "under its own name",
                   all(w in idl and idl[w][0].replace("::", ".") == w
                       for w in want),
                   f"{ {w: idl.get(w, ('-',))[0] for w in want} }"))


def pin_vs_recording(checks: list) -> None:
    recorded = {}
    with open(FOUNTAIN, "rb") as f:
        for _i, sch in sorted(make_reader(f).get_summary().schemas.items()):
            if sch.encoding == "omgidl":
                recorded[sch.name] = sch.data
    mine = dict(pin.schema_records())
    same = {n: recorded.get(n) == mine.get(n)
            for n in sorted(set(recorded) | set(mine))}
    checks.append((f"the pin flattens to the same bytes "
                   f"{FOUNTAIN.name} embeds, for all "
                   f"{len(recorded)} of its schema records",
                   bool(recorded) and all(same.values()),
                   f"{ {n: ('same' if v else 'DIFFERS') for n, v in same.items()} }"))
    digests = sorted({hashlib.sha256(d).hexdigest()[:12]
                      for d in mine.values()})
    print(f"  note the pin's schema records: "
          + ", ".join(f"{n.split('::')[-1]}={hashlib.sha256(d).hexdigest()[:12]}"
                      for n, d in sorted(mine.items())))
    assert digests


async def empty_loop(checks: list) -> None:
    pub = json.loads(PUB.read_text())
    duck = json.loads(DUCK.read_text())
    kinds = [("announce", pub["service"]), ("content_announce", pub["content"]),
             ("geo_anchor", duck["anchor"])]
    man = duck["manifest"]

    with tempfile.TemporaryDirectory() as tmp:
        jp = Path(tmp) / "registry.mcap"
        st = gateway.build_state(None)
        checks.append((f"an empty registry knows its types before anything is "
                       f"published: {st.type_source.split(' under ')[0]}",
                       st.model is not None and st.type_source.startswith(
                           "vendored IDL"), f"{st.type_source!r}"))
        checks.append(("…and serves no resources yet, because an empty "
                       "registry has none",
                       not st.resources and not st.latched,
                       f"{len(st.resources)} resource(s)"))

        gw = await Gateway(st, advertise=DEMO, publish=True,
                           journal=jp).start()
        served = {}
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": f"Bearer {TOKEN}"}
                for name, spec in kinds:
                    url = f"{spec['path']}/{spec['key'].replace('/', '%2F')}"
                    async with s.put(gw.base + url, json=spec["record"],
                                     headers=h) as r:
                        body = await r.json()
                        checks.append((f"publish {name} into the empty "
                                       "registry: 201, and the served "
                                       "document is the authored record",
                                       r.status == 201
                                       and body == spec["record"],
                                       f"{r.status}: {str(body)[:120]}"))
                    served[url] = body
                async with s.put(gw.base + gateway.RESOLVE_PATH, json=man,
                                 headers=h) as r:
                    checks.append(("publish the duck's content manifest: 201",
                                   r.status == 201, f"{r.status}"))
                # Found by search, by the cell the authored announce declares.
                # The cell the authored circle falls in, computed here from
                # the input's own centre rather than read off the gateway.
                lon, lat, _alt = \
                    pub["service"]["record"]["coverage"][0]["circle_center"]
                cell = geohash(lat, lon, 6)
                async with s.get(gw.base + "/.well-known/spatialdds/search",
                                 params={"geohash": cell}) as r:
                    doc = await r.json()
                ids = [m["service"]["service_id"] for m in doc["results"]]
                checks.append((f"search on the cell the authored announce "
                               f"declares ({cell}) finds "
                               f"{pub['service']['key']!r}",
                               pub["service"]["key"] in ids, f"{ids}"))
                # And by resolution, which is the half that used to be lost.
                async with s.get(gw.base + gateway.RESOLVE_PATH,
                                 params={"uri": man["id"]}) as r:
                    got = await r.json()
                checks.append(("the published manifest resolves per §7.5.2",
                               r.status == 200 and got == man,
                               f"{r.status}"))
        finally:
            await gw.stop()

        # Restart from the journal alone: no recording, no manifest
        # directory, nothing but the file the registry wrote.
        st2 = gateway.build_state(None)
        gw2 = await Gateway(st2, advertise=DEMO, publish=True,
                            journal=jp).start()
        try:
            async with aiohttp.ClientSession() as s:
                same = []
                for url, before in served.items():
                    async with s.get(gw2.base + url) as r:
                        same.append(await r.json() == before)
                checks.append((f"after a restart from the journal alone, all "
                               f"{len(served)} published records are served "
                               "identically",
                               all(same) and len(same) == len(kinds),
                               f"{same}"))
                async with s.get(gw2.base + gateway.RESOLVE_PATH,
                                 params={"uri": man["id"]}) as r:
                    checks.append(("…and the published manifest still "
                                   "resolves, which is what made manifests "
                                   "journalled records in 0.2.0",
                                   r.status == 200 and await r.json() == man,
                                   f"{r.status}"))
        finally:
            await gw2.stop()

        # The journal is a recording: read it with the ordinary reader, with
        # no knowledge that a registry wrote it.
        st3 = gateway.build_state(jp)
        keys = {t: sorted(v) for t, v in st3.latched.items()}
        checks.append((f"the journal reads back through the ordinary reader "
                       f"as a recording: {len(st3.resources)} resource(s), "
                       "types from its own embedded IDL",
                       len(st3.resources) == len(kinds)
                       and st3.type_source.startswith("the recording's own"),
                       f"{st3.type_source!r}"))
        want = {f"spatialdds{spec['path']}": [spec["key"]]
                for _n, spec in kinds}
        checks.append(("…with the instances the registry accepted, under the "
                       "keys the inputs declare",
                       keys == want, f"{keys}"))
        embedded = {}
        with open(jp, "rb") as f:
            for _i, sch in sorted(make_reader(f).get_summary().schemas.items()):
                embedded[sch.name] = sch.data
        mine = dict(pin.schema_records())
        checks.append((f"and the IDL it embeds is the pin's, byte for byte "
                       f"({len(embedded)} schema record(s))",
                       bool(embedded) and all(embedded[n] == mine[n]
                                              for n in embedded),
                       f"{sorted(embedded)}"))


def absence_refuses(checks: list) -> None:
    with tempfile.TemporaryDirectory() as empty:
        st = gateway.build_state(None, idl_dir=Path(empty))
        checks.append(("with no IDL where the pin should be, the gateway is "
                       "typeless rather than pretending",
                       st.model is None and "none" in st.type_source,
                       f"{st.type_source!r}"))
    # And the refusal a person would actually meet, through the command line.
    code = ("import sys, pathlib\n"
            "from spatialdds.server import pin\n"
            "pin.IDL_DIR = pathlib.Path('/nonexistent-idl-for-this-gate')\n"
            "from spatialdds import serve\n"
            # --no-journal, because a journal carries its own IDL: replaying
            # one would hand the endpoint the types this probe is taking away,
            # and the probe would be measuring the journal rather than the pin.
            "serve.main(['--publish', '--no-journal', '--authority',\n"
            "            'x.example'])\n")
    # With a timeout, because the thing being probed is a refusal: if the
    # refusal does not fire, `serve` goes on to serve, and a gate that waits
    # for a server to exit waits forever. One run did exactly that.
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60,
                           env={"PATH": "/usr/bin:/bin",
                                "PYTHONPATH": str(LIB)})
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        out = ("(timed out: the probe did not refuse, so `serve` went on to "
               "serve with types it should not have had)")
    checks.append(("…and `--publish` then refuses in a sentence naming the "
                   "files it looked for and where",
                   "needs the types" in out and "nonexistent-idl-for-this-gate"
                   in out and "core.idl" in out,
                   f"{out[-300:]!r}"))


async def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    pin_vs_tables(checks)
    pin_vs_recording(checks)
    await empty_loop(checks)
    absence_refuses(checks)
    return report("a registry that starts empty", checks,
                  "the pin is the type source, and the loop closes through a "
                  "restart")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
