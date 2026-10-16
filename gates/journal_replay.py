#!/usr/bin/env python3
"""Restart is the same state, and the journal is a recording.

Publish several records, dispose one, then stand the endpoint up again from the
journal and byte-compare what it serves. The comparison is on the served bytes
rather than on internal structures, because what a reader sees is the only
thing a restart has to preserve.

Two further properties, because "the journal is a recording" is a claim and not
a hope: the file is a valid MCAP with embedded omgidl schemas readable by any
tool, and a gateway given the journal and *no* recording learns its types from
the journal itself.

Usage:  gates/journal_replay.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path

from mcap.reader import make_reader                            # noqa: E402

from publish_common import (aiohttp, authored, authority_base, delete,   # noqa: E402
                            get, put, seeded)
from harness import Gateway, report                            # noqa: E402
from spatialdds.server import gateway                             # noqa: E402


async def served(gw, paths: list[str]) -> dict[str, bytes]:
    out = {}
    async with aiohttp.ClientSession() as s:
        for p in paths:
            _st, _h, raw = await get(s, gw.base, p)
            out[p] = raw
    return out


async def main() -> int:
    doc = authored()
    base = authority_base(doc)
    checks: list[tuple[str, bool, str]] = []
    paths = [doc["service"]["path"], doc["content"]["path"]]

    with tempfile.TemporaryDirectory() as d:
        jr = Path(d) / "journal.mcap"

        gw = await Gateway(seeded(doc), advertise=base, publish=True,
                           journal=jr).start()
        async with aiohttp.ClientSession() as s:
            # Three publishes: two that stay and one that is disposed, so the
            # journal has to replay an order and not merely a set.
            for kind in ("service", "content"):
                spec = doc[kind]
                await put(s, gw.base,
                          f"{spec['path']}/{spec['key'].replace('/', '%2F')}",
                          spec["record"])
            doomed = copy.deepcopy(doc["service"]["record"])
            doomed["service_id"] = "transient-service"
            dpath = f"{doc['service']['path']}/transient-service"
            st_, _, _ = await put(s, gw.base, dpath, doomed)
            checks.append(("a third record is published (201)", st_ == 201,
                           f"got {st_}"))
            std, _, _ = await delete(s, gw.base, dpath)
            checks.append(("and disposed (204)", std == 204, f"got {std}"))
        before = await served(gw, paths)
        await gw.stop()

        # Same inputs, fresh process state: seed plus journal.
        gw2 = await Gateway(seeded(doc), advertise=base, publish=True,
                            journal=jr).start()
        after = await served(gw2, paths)
        await gw2.stop()
        for p in paths:
            checks.append((f"restart serves {p} byte-identically",
                           before[p] == after[p],
                           f"{len(before[p])} vs {len(after[p])} bytes"))
        gone = [i for i in json.loads(after[doc["service"]["path"]])
                if i.get("service_id") == "transient-service"]
        checks.append(("the disposed record does not come back",
                       not gone, f"{len(gone)} copy(ies) returned"))
        kept = sorted(i.get("service_id")
                      for i in json.loads(after[doc["service"]["path"]]))
        checks.append((f"and the ones that should survive do ({kept})",
                       doc["service"]["record"]["service_id"] in kept,
                       f"{kept}"))

        # The journal as an artefact, not as an internal format.
        with open(jr, "rb") as f:
            r = make_reader(f)
            summary = r.get_summary()
            schemas = {s.name: s.encoding for s in summary.schemas.values()}
        with open(jr, "rb") as f:
            msgs = list(make_reader(f).iter_messages())
        with open(jr, "rb") as f:
            md = [m.name for m in make_reader(f).iter_metadata()]
        checks.append((f"the journal is a valid MCAP carrying omgidl schemas "
                       f"({sorted(schemas)})",
                       bool(schemas)
                       and set(schemas.values()) == {"omgidl"},
                       f"{schemas}"))
        checks.append((f"its messages are the accepted publishes "
                       f"({len(msgs)})", len(msgs) == 3, f"{len(msgs)}"))
        checks.append((f"the dispose is recorded as a labeled metadata record "
                       f"({md})", md.count("spatialdds-web:dispose") == 1,
                       f"{md}"))

        # A gateway with the journal and no recording at all.
        st3 = gateway.build_state(None)
        gw3 = await Gateway(st3, advertise=base, publish=True,
                            journal=jr).start()
        only = await served(gw3, paths)
        await gw3.stop()
        ids = sorted(i.get("service_id")
                     for i in json.loads(only[doc["service"]["path"]]))
        checks.append((f"a gateway given only the journal learns its types "
                       f"from it and serves what was published ({ids})",
                       ids == [doc["service"]["record"]["service_id"]],
                       f"{ids}"))
        cids = sorted(i.get("content_id")
                      for i in json.loads(only[doc["content"]["path"]]))
        checks.append((f"including the content record ({cids})",
                       cids == [doc["content"]["key"]], f"{cids}"))

    return report("journal replay", checks,
                  "restart is the same state, and a backup is a recording")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
