"""Issue, list and revoke write tokens in a registry's token table.

    python -m spatialdds.tokens --file tokens.json add james \\
        --provider openarcloud.org --scene ut-austin-south-mall
    python -m spatialdds.tokens --file tokens.json list
    python -m spatialdds.tokens --file tokens.json revoke james

`add` prints the new token once, on its own line, and stores only its SHA-256;
lose it and the answer is `revoke` then `add`, never recovery. A running
registry picks the change up on SIGHUP (`systemctl reload`), without dropping a
connection. The table format is documented in `spatialdds.server.tenancy`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .server import tenancy


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m spatialdds.tokens",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, required=True,
                    help="the token table, created if absent (mode 0600)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="issue a token for a new principal")
    a.add_argument("name")
    a.add_argument("--provider", required=True,
                   help="the principal's provider identity, domain-shaped "
                        "(recorded on every journal entry it writes)")
    a.add_argument("--scene", action="append", required=True,
                   help="a scene this principal may write; repeat for more, "
                        "or '*' for every scene that exists")
    a.add_argument("--may-create-scenes", action="store_true",
                   help="may write scenes that do not exist yet")
    sub.add_parser("list", help="principals and their grants (no tokens)")
    r = sub.add_parser("revoke", help="remove a principal; its token stops "
                                      "working at the next reload")
    r.add_argument("name")
    args = ap.parse_args(argv)

    if args.file.exists():
        table = tenancy.TokenTable.load(args.file)
    else:
        table = tenancy.TokenTable([], args.file)

    if args.cmd == "list":
        for p in table.principals():
            print(f"{p.name:16s} {p.provider:28s} "
                  f"{','.join(sorted(p.scenes)):32s} "
                  f"{'may create scenes' if p.may_create_scenes else ''}")
        return 0
    if args.cmd == "revoke":
        before = len(table.rows)
        table.rows = [(h, p) for h, p in table.rows if p.name != args.name]
        if len(table.rows) == before:
            print(f"no principal named {args.name!r}", file=sys.stderr)
            return 1
        table.save()
        print(f"revoked {args.name}; reload the registry to apply",
              file=sys.stderr)
        return 0
    if any(p.name == args.name for p in table.principals()):
        print(f"principal {args.name!r} exists; revoke it first to re-issue",
              file=sys.stderr)
        return 1
    token = tenancy.issue()
    table.rows.append((tenancy.digest(token), tenancy.Principal(
        args.name, args.provider, frozenset(args.scene),
        args.may_create_scenes)))
    table.save()
    print(f"issued {args.name} for {', '.join(sorted(args.scene))}; the token "
          f"follows and is not stored. Reload the registry to apply.",
          file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
