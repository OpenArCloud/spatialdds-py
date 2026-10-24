"""Operator actions on a registry's journal, offline.

    python -m spatialdds.journal --journal registry.mcap list
    python -m spatialdds.journal --journal registry.mcap reset-scene wow-world

`reset-scene` is the per-lane reset: stop the registry, run it, start the
registry. It removes that scene's entries and writes every other entry back
with its own sequence number and bytes, so every other lane serves exactly
what it served before, and the reset lane returns to its seed (or to empty, for
a lane with no seed). Run it against a stopped registry: a running one holds
the journal in memory and would write the removed entries back at its next
accepted write.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .server import publish


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m spatialdds.journal",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--journal", type=Path, required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="entries per scene and principal")
    r = sub.add_parser("reset-scene", help="remove one scene's entries")
    r.add_argument("scene")
    args = ap.parse_args(argv)
    if not args.journal.is_file():
        print(f"no journal at {args.journal}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        items, _idl = publish.read_entries(args.journal)
        c = Counter((e.get("scene", ""), e.get("principal", ""), e["kind"])
                    for _s, e in items)
        for (scene, who, kind), n in sorted(c.items()):
            print(f"{scene or '(none)':28s} {who or '(unattributed)':16s} "
                  f"{kind:18s} {n}")
        return 0
    removed, kept = publish.reset_scene(args.journal, args.scene)
    print(f"reset {args.scene}: {removed} entr(ies) removed, {kept} kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
