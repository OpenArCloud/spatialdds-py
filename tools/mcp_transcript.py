#!/usr/bin/env python3
"""Record a real MCP client's session with `spatialdds-mcp`, message by message.

The client is the official MCP SDK's `mcp.Client`; the transcript is every
JSON-RPC message that crossed the transport, both ways, as JSON lines, so it
can be read back without either program:

    python3 tools/mcp_transcript.py stdio-sandbox
    python3 tools/mcp_transcript.py stdio-venue-live
    python3 tools/mcp_transcript.py http-local
    python3 tools/mcp_transcript.py --sums       # rewrite samples/mcp/SHA256SUMS

Each one calls all five tools. `stdio-venue-live` and `http-local` read
SpatialDDS-demo's venue at http://127.0.0.1:8089, which must be running; after
the `get_claims` call each also records the binding's own answer for the
claims lane, read the same way (no token), so that a reader can hold the
server's verbatim claims against what the binding served. The transcripts are
fixtures: `gates/mcp_server.py` checks them against SHA256SUMS and against
what they say.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

import _mcp                                                   # noqa: E402

SANDBOX_SCENE = "https://demo.spatialdds.org/ut-austin-south-mall"
VENUE = "http://127.0.0.1:8089"
CLAIMS_LANE = "/ut-austin-littlefield/world/oarc.relationship/v1"

SANDBOX_CALLS = [
    ("list_scenes", {}),
    ("describe_scene", {"scene": "ut-austin-south-mall"}),
    ("get_entities", {"scene": "ut-austin-south-mall"}),
    ("get_claims", {"scene": "ut-austin-south-mall"}),
    ("get_entity", {"scene": "ut-austin-south-mall", "id": "ent:bench"}),
]
VENUE_CALLS = [
    ("list_scenes", {}),
    ("describe_scene", {"scene": "ut-austin-littlefield"}),
    ("get_entities", {"scene": "ut-austin-littlefield",
                      "near": {"x": 12.0, "y": -12.4, "radius_m": 2.0}}),
    ("get_claims", {"scene": "ut-austin-littlefield"}),
    ("get_entity", {"scene": "ut-austin-littlefield",
                    "id": "ent:pond:littlefield"}),
]
HTTP_CALLS = [
    ("list_scenes", {}),
    ("describe_scene", {"scene": "ut-austin-south-mall"}),
    ("get_entities", {"scene": "ut-austin-littlefield",
                      "type": "Q851478"}),
    ("get_claims", {"scene": "ut-austin-littlefield",
                    "subject": "ent:duck:east"}),
    ("get_entity", {"scene": "ut-austin-littlefield", "id": "ent:robot:tb3"}),
]
RECORDINGS = {
    "stdio-sandbox": ("stdio", ["--scene", SANDBOX_SCENE], SANDBOX_CALLS),
    "stdio-venue-live": ("stdio", ["--scene", VENUE], VENUE_CALLS),
    "http-local": ("http", ["--scene", SANDBOX_SCENE, "--scene", VENUE],
                   HTTP_CALLS),
}


def binding(log: list, url: str) -> None:
    """The binding's own answer, read as the server reads: no token."""
    with urllib.request.urlopen(url, timeout=15) as r:
        log.append({"from": "binding", "t": round(time.time(), 3),
                    "url": url, "status": r.status,
                    "body": json.loads(r.read())})


async def record(name: str) -> Path:
    transport, args, calls = RECORDINGS[name]
    log: list = []
    http = None
    if transport == "stdio":
        client = _mcp.stdio(args, log)
        where = "python -m spatialdds.mcp_server " + " ".join(args)
    else:
        http = _mcp.HttpServer(["--throttle", "0", *args])
        client = _mcp.http(http.url, log)
        where = (f"python -m spatialdds.mcp_server --http "
                 f"127.0.0.1:{http.port} --throttle 0 " + " ".join(args))
    try:
        async with client as c:
            # A server that has been running a moment: its watches hold
            # their replay before the first call.
            await asyncio.sleep(3)
            for tool, arguments in calls:
                await c.call_tool(tool, arguments)
                if tool == "get_claims" and VENUE in args:
                    binding(log, VENUE + CLAIMS_LANE)
    finally:
        if http is not None:
            http.stop()
    from importlib.metadata import version
    head = {"transcript": name, "transport": transport,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "server": where, "client": f"mcp {version('mcp')} mcp.Client",
            "calls": [{"tool": t, "arguments": a} for t, a in calls]}
    out = _mcp.FIXTURES / f"transcript-{name}.jsonl"
    out.write_text("\n".join(json.dumps(x, sort_keys=True)
                             for x in [head, *log]) + "\n")
    return out


def sums() -> Path:
    names = sorted(p.name for p in _mcp.FIXTURES.iterdir()
                   if p.name != "SHA256SUMS")
    out = _mcp.FIXTURES / "SHA256SUMS"
    out.write_text("".join(
        f"{hashlib.sha256((_mcp.FIXTURES / n).read_bytes()).hexdigest()}  {n}\n"
        for n in names))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", choices=list(RECORDINGS))
    ap.add_argument("--sums", action="store_true")
    a = ap.parse_args()
    for n in a.names:
        out = asyncio.run(record(n))
        lines = out.read_text().splitlines()
        print(f"{out.relative_to(ROOT)}: {len(lines) - 1} message(s)")
    if a.sums or a.names:
        print(f"{sums().relative_to(ROOT)} rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
