# The MCP server

`spatialdds-mcp` lets any MCP client (Claude Desktop, Claude Code, or any agent
framework that speaks the Model Context Protocol) read live SpatialDDS world
models: what is in a scene, what is claimed about it, who claims it, and how
fresh that is. It is read-only, and it is a client of the web binding and
nothing else: it reads over HTTPS and WebSocket exactly what any reader without
a token reads, holds no credential, and never touches DDS.

Every record it returns says who asserted it, on what basis, when, and how old
that is, and what the binding has said about its writer since the server began
watching. It never merges, resolves or ranks: two claims about one proposition
come back side by side, and what to believe is the reader's call.

Every example on this page is run by this repository's gate,
`gates/mcp_guide.py`, through the official MCP client, and its output is
compared with the output printed here. Ages and the times a watch began differ
from run to run; the gate checks each against its definition instead.

## Install

The server is an extra, so the client library's own install is unchanged. It
needs Python 3.10 or newer, which the MCP SDK requires.

<!-- mcp: install -->
```sh
pip install "spatialdds[mcp]==0.8.3"
```

This installs `spatialdds-mcp`. Point it at one or more scenes with `--scene`:
a binding base URL (every scene its search lists), or a base URL and one scene.

## Claude Desktop

Add the server to `claude_desktop_config.json` (Settings, Developer, Edit
Config) and restart Claude Desktop:

<!-- mcp: config claude-desktop -->
```json
{
  "mcpServers": {
    "spatialdds": {
      "command": "spatialdds-mcp",
      "args": ["--scene", "https://demo.spatialdds.org/ut-austin-south-mall"]
    }
  }
}
```

## Claude Code

Save this as `.mcp.json` in your project's root; Claude Code offers the server
the next time it starts there:

<!-- mcp: config claude-code -->
```json
{
  "mcpServers": {
    "spatialdds": {
      "type": "stdio",
      "command": "spatialdds-mcp",
      "args": ["--scene", "https://demo.spatialdds.org/ut-austin-south-mall"]
    }
  }
}
```

Either way, ask the model about the scene: "What does the sandbox's sample
scene serve?" is enough for it to start with `list_scenes`.

## The tools

Five tools, read-only, with names that are stable across releases of the
package. Each answers with two text blocks: one line per record, for reading,
and the same answer as compact JSON, which is also the tool's structured
result. The examples below show the first block; the JSON is shown once, under
"Every field, verbatim".

| Tool | Arguments | Answers with |
| --- | --- | --- |
| `list_scenes` | none | Each scene: its endpoint, its lane and type counts, and how many lanes a reader without a token can read |
| `describe_scene` | `scene` | Each lane: its type, access class, durability, record count and published schema |
| `get_entities` | `scene`, optional `type`, optional `near` | The scene's entities, with pose, extent, asserter, basis, stamp and age |
| `get_claims` | `scene`, optional `subject`, `predicate`, `asserter` | Relationship claims, every field verbatim, each with a one-line rendering |
| `get_entity` | `scene`, `id` | One entity, and every claim in which it is the subject or the object |

`scene` is a scene's name (`ut-austin-south-mall`) or, where two endpoints
serve the same name, its full key (`demo.spatialdds.org/ut-austin-south-mall`).

### Against the sandbox

These run against the public sandbox, `https://demo.spatialdds.org`, with the
configuration above.

**`list_scenes`.**

<!-- mcp: call sandbox -->
```json
{"tool": "list_scenes", "arguments": {}}
```

```text
demo.spatialdds.org/ut-austin-south-mall: 3 lane(s) of 3 type(s) (announce, content_announce, geo_anchor), 3 readable without a token, from fountain-anchors
```

**`describe_scene`.** Every lane, from the binding's search, the resources
themselves, and the schemas index:

<!-- mcp: call sandbox -->
```json
{"tool": "describe_scene", "arguments": {"scene": "ut-austin-south-mall"}}
```

```text
/ut-austin-south-mall/content/content_announce/v1: spatial.disco.ContentAnnounce, public-cacheable, 2 record(s)
/ut-austin-south-mall/discovery/announce/v1: spatial.disco.Announce, public-cacheable, 1 record(s)
/ut-austin-south-mall/frames/geo_anchor/v1: spatial.core.GeoAnchor, public-cacheable, 5 record(s), takes commands
```

**`get_entities`, `get_claims` and `get_entity`.** The sandbox's sample scene
is content and anchors, and serves no world lane, so the world tools say so
rather than answer with nothing:

<!-- mcp: call sandbox -->
```json
{"tool": "get_entities", "arguments": {"scene": "ut-austin-south-mall"}}
```

```text
demo.spatialdds.org/ut-austin-south-mall serves no entity lane: its service manifest lists none
```

<!-- mcp: call sandbox -->
```json
{"tool": "get_claims", "arguments": {"scene": "ut-austin-south-mall"}}
```

```text
demo.spatialdds.org/ut-austin-south-mall serves no claim lane: its service manifest lists none
```

<!-- mcp: call sandbox -->
```json
{"tool": "get_entity", "arguments": {"scene": "ut-austin-south-mall", "id": "ent:bench"}}
```

```text
demo.spatialdds.org/ut-austin-south-mall serves no entity lane: its service manifest lists none
demo.spatialdds.org/ut-austin-south-mall serves no claim lane: its service manifest lists none
```

The sandbox's integrators do publish entities and claims, each into their own
scene, but the sandbox serves world lanes only to a reader holding a token
(its contract, in the integrator guide's last section), and this server holds
none. It describes such a lane as `operator-scoped` and does not read it.

### Against a venue that serves its world

SpatialDDS-demo's Littlefield venue serves its world lanes to everyone: a
fountain, its pond, three rubber ducks and a simulated robot, which its
operator judged carry nothing about a person. Run the demo, then point the
server at its OWM binding:

<!-- mcp: config venue -->
```json
{
  "mcpServers": {
    "littlefield": {
      "command": "spatialdds-mcp",
      "args": ["--scene", "http://localhost:8089"]
    }
  }
}
```

The examples in this section ran against a copy of that venue, captured from
its binding on 2026-11-09 (`samples/mcp/venue.json`) and served by the gate
the way the demo's bridge serves the live one.

**`get_entities`, near a point.** `near` is a point and a radius in the
scene's frame, measured to an entity's box where it has one and to its pose
where it does not. Here, everything within 2 m of the middle of the pond:

<!-- mcp: call venue -->
```json
{"tool": "get_entities", "arguments": {"scene": "ut-austin-littlefield", "near": {"x": 12.0, "y": -12.4, "radius_m": 2.0}}}
```

```text
ent:fountain:littlefield (Littlefield Fountain): observed by svc:model:demo/venue, at (11.7, -5.2, -1.4) in map/ut-littlefield-fountain, box 28.2 x 32.5 x 5 m, stamped 2026-10-31T23:44:14Z (8.2 d ago), 0 m from the point
ent:pond:littlefield (Pond): declared by svc:model:demo/venue, at (12, -12.4, -1.5) in map/ut-littlefield-fountain, box 27 x 15.2 x 1 m, stamped 2026-10-31T23:44:14Z (8.2 d ago), 0 m from the point
ent:shallows:littlefield (Shallows): declared by svc:model:demo/venue, at (14.8, -14, -1.5) in map/ut-littlefield-fountain, box 10.5 x 8 x 1 m, stamped 2026-10-31T23:44:14Z (8.2 d ago), 0 m from the point
ent:duck:catalog-pose (Waddles): authored by svc:model:demo/venue, at (11.5, -11.8, -1.4) in map/ut-littlefield-fountain, stamped 2026-11-06T09:38:30Z (2.8 d ago), 0.7 m from the point
ent:duck:west (Bobbin): authored by svc:model:demo/venue, at (10.6, -12.4, -1.4) in map/ut-littlefield-fountain, stamped 2026-11-06T09:38:30Z (2.8 d ago), 1.4 m from the point
ent:duck:east (Skipper): authored by svc:model:demo/venue, at (11.7, -12.5, -1.4) in map/ut-littlefield-fountain, stamped 2026-11-06T09:38:29Z (2.8 d ago), 0.3 m from the point
ent:pond:observed (Pond): derived by svc:fusion:demo/pondwatch, at (11.8, -12.2, -1.5) in map/ut-littlefield-fountain, box 28.7 x 17.1 x 1 m, stamped 2026-11-09T04:45:54Z (6 min ago), 0 m from the point
```

An entity's asserter is its `source_id`: the entity's own statement of who
wrote it, carried as written. A claim's asserter is the claim's own member.

**`get_claims`.** Every claim the venue serves:

<!-- mcp: call venue -->
```json
{"tool": "get_claims", "arguments": {"scene": "ut-austin-littlefield"}}
```

```text
svc:model:demo/venue declares ent:pond:littlefield P361 ent:fountain:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:model:demo/venue declares ent:shallows:littlefield P361 ent:pond:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:mover:demo/ducks declares ent:duck:catalog-pose geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:mover:demo/ducks declares ent:duck:east geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:mover:demo/ducks declares ent:duck:west geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:claims:demo/near derives ent:robot:tb3 near#15m ent:duck:catalog-pose, valid from 2026-11-08T22:51:34Z, derived from 2 record(s), stamped 2026-11-08T22:51:34Z (6.0 h ago)
svc:claims:demo/near derives ent:robot:tb3 near#15m ent:duck:east, valid from 2026-11-08T22:51:34Z, derived from 2 record(s), stamped 2026-11-08T22:51:34Z (6.0 h ago)
svc:claims:demo/near derives ent:robot:tb3 near#15m ent:duck:west, valid from 2026-11-08T22:51:34Z, derived from 2 record(s), stamped 2026-11-08T22:51:34Z (6.0 h ago)
```

**`get_entity`.** One entity, and every claim that names it:

<!-- mcp: call venue -->
```json
{"tool": "get_entity", "arguments": {"scene": "ut-austin-littlefield", "id": "ent:duck:east"}}
```

```text
ent:duck:east (Skipper): authored by svc:model:demo/venue, at (11.7, -12.5, -1.4) in map/ut-littlefield-fountain, stamped 2026-11-06T09:38:29Z (2.8 d ago)
svc:mover:demo/ducks declares ent:duck:east geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:claims:demo/near derives ent:robot:tb3 near#15m ent:duck:east, valid from 2026-11-08T22:51:34Z, derived from 2 record(s), stamped 2026-11-08T22:51:34Z (6.0 h ago)
```

**`describe_scene`.** The venue's lanes. Its command lane is operator-scoped,
so it is listed and not read; the two world lanes are watched:

<!-- mcp: call venue -->
```json
{"tool": "describe_scene", "arguments": {"scene": "ut-austin-littlefield"}}
```

```text
/ut-austin-littlefield/frames/frame_transform/v1: spatial.core.FrameTransform, public-cacheable, 1 record(s)
/ut-austin-littlefield/world/entity/v1: spatial.owm.Entity, public-cacheable, 9 record(s), watched since 2026-11-09T04:51:27Z
/ut-austin-littlefield/world/model_command/v1: model_command, operator-scoped, not readable without a token, which this server does not hold, takes commands
/ut-austin-littlefield/world/model_pose/v1: spatial.owm.ModelPose, public-cacheable, a stream: the binding holds no records to count
/ut-austin-littlefield/world/oarc.relationship/v1: oarc_owm.Relationship, public-cacheable, 8 record(s), watched since 2026-11-09T04:51:27Z
```

## Who said so, and how fresh

**Age is arithmetic, not a verdict.** Each record's age is now minus its
stamp. A declaration eight days old is not stale; an observation eight days
old may be. The server reports both the same way and leaves the judgement to
the reader, which is why every line names the basis.

**Liveliness is what the binding said.** The web binding carries a writer's
liveliness only as an event on a lane's WebSocket. A `GET` carries none, and a
subscription that opens later is not told what was lost before it. So the
server holds one subscription to each public world lane from the moment it
starts, and reports what it heard about each record's writer:

| `liveliness` | Meaning |
| --- | --- |
| `lost` | The binding said the writer went away, and nothing has been written since. The line says "nobody asserts this now" |
| `reported` | A live write, or the binding saying the writer is alive, since the watch began |
| `unreported` | Nothing either way since the watch began. A record replayed when the watch began is not evidence: the latched set is exactly what a crashed writer leaves behind |
| `unwatched` | There is no subscription to this lane, and the JSON says why |

When the duck mover's writer goes away, its claims stay, and say so. (In the
copy, the venue's binding is told that writer's liveliness ended for each
claim it wrote, which is what the demo's bridge passes on when a writer on its
bus stops.)

<!-- mcp: venue writer-lost oarc.relationship svc:mover:demo/ducks -->
<!-- mcp: call venue -->
```json
{"tool": "get_claims", "arguments": {"scene": "ut-austin-littlefield", "asserter": "svc:mover:demo/ducks"}}
```

```text
svc:mover:demo/ducks declares ent:duck:catalog-pose geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago); nobody asserts this now (the binding reported its writer gone at 2026-11-09T04:51:29Z)
svc:mover:demo/ducks declares ent:duck:east geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago); nobody asserts this now (the binding reported its writer gone at 2026-11-09T04:51:29Z)
svc:mover:demo/ducks declares ent:duck:west geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago); nobody asserts this now (the binding reported its writer gone at 2026-11-09T04:51:29Z)
```

**Withdrawn is not dropped.** When a writer withdraws a claim, the binding
sends its last state, `RETIRED` with a reason, and removes it, so a `GET`
stops serving it. The server keeps what it heard for an hour, marked as no
longer served:

<!-- mcp: venue withdraw oarc.relationship rel:svc:model:demo.venue:161b39af985a5167 pond drained for cleaning -->
<!-- mcp: call venue -->
```json
{"tool": "get_claims", "arguments": {"scene": "ut-austin-littlefield", "subject": "ent:pond:littlefield"}}
```

```text
svc:model:demo/venue declares ent:shallows:littlefield P361 ent:pond:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago)
svc:model:demo/venue declares ent:pond:littlefield P361 ent:fountain:littlefield, retired: pond drained for cleaning, stamped 2026-10-31T23:44:14Z (8.2 d ago); withdrawn at 2026-11-09T04:51:29Z, no longer served
```

## Two asserters, side by side

The server never decides between claims. When a second asserter, here a
camera, observes the same proposition the duck mover declares, both come back,
together, as claims:

<!-- mcp: venue write oarc.relationship -->
```json
{"id": "rel:svc:camera:demo.north:within-east", "subject_ref": "ent:duck:east",
 "predicate": "http://www.opengis.net/ont/geosparql#sfWithin",
 "object_ref": "ent:shallows:littlefield", "basis": "OBSERVED",
 "asserter": "svc:camera:demo/north", "has_confidence": true, "confidence": 0.62,
 "derived_from": [], "stamp": {"sec": "1794199000", "nanosec": 0},
 "has_valid_from": false, "valid_from": {"sec": "0", "nanosec": 0},
 "has_valid_until": false, "valid_until": {"sec": "0", "nanosec": 0},
 "meta": [], "state": "CREATED", "state_reason": ""}
```

<!-- mcp: call venue -->
```json
{"tool": "get_claims", "arguments": {"scene": "ut-austin-littlefield", "subject": "ent:duck:east"}}
```

```text
svc:mover:demo/ducks declares ent:duck:east geosparql#sfWithin ent:shallows:littlefield, stamped 2026-10-31T23:44:14Z (8.2 d ago); nobody asserts this now (the binding reported its writer gone at 2026-11-09T04:51:29Z)
svc:camera:demo/north observes ent:duck:east geosparql#sfWithin ent:shallows:littlefield, confidence 0.62, stamped 2026-11-09T04:36:40Z (15 min ago)
svc:claims:demo/near derives ent:robot:tb3 near#15m ent:duck:east, valid from 2026-11-08T22:51:34Z, derived from 2 record(s), stamped 2026-11-08T22:51:34Z (6.0 h ago)
```

## Every field, verbatim

The JSON block of the same answer carries each claim exactly as the binding
served it, under `claim`, beside what the server computed: the proposition,
the stamp as a time, the age, and the liveliness. The camera's claim:

<!-- mcp: call venue json claims.1 -->
```json
{"tool": "get_claims", "arguments": {"scene": "ut-austin-littlefield", "subject": "ent:duck:east"}}
```

```json
{
 "age_s": 890.6,
 "claim": {
  "asserter": "svc:camera:demo/north",
  "basis": "OBSERVED",
  "confidence": 0.62,
  "derived_from": [],
  "has_confidence": true,
  "has_valid_from": false,
  "has_valid_until": false,
  "id": "rel:svc:camera:demo.north:within-east",
  "meta": [],
  "object_ref": "ent:shallows:littlefield",
  "predicate": "http://www.opengis.net/ont/geosparql#sfWithin",
  "stamp": {
   "nanosec": 0,
   "sec": "1794199000"
  },
  "state": "CREATED",
  "state_reason": "",
  "subject_ref": "ent:duck:east",
  "valid_from": {
   "nanosec": 0,
   "sec": "0"
  },
  "valid_until": {
   "nanosec": 0,
   "sec": "0"
  }
 },
 "liveliness": "reported",
 "liveliness_at": "2026-11-09T04:51:30Z",
 "proposition": [
  "ent:duck:east",
  "http://www.opengis.net/ont/geosparql#sfWithin",
  "ent:shallows:littlefield"
 ],
 "rendering": "svc:camera:demo/north observes ent:duck:east geosparql#sfWithin ent:shallows:littlefield, confidence 0.62, stamped 2026-11-09T04:36:40Z (15 min ago)",
 "served": true,
 "stamped": "2026-11-09T04:36:40Z"
}
```

## Over HTTP

`--http HOST:PORT` serves the same tools over streamable HTTP at
`http://HOST:PORT/mcp`, for clients that connect to a URL rather than launch a
command:

<!-- mcp: http start -->
```sh
spatialdds-mcp --http 127.0.0.1:8790 --scene https://demo.spatialdds.org/ut-austin-south-mall
```

From another terminal, with the MCP SDK's own client:

<!-- mcp: http python -->
```python
import asyncio
from mcp import Client

async def main():
    async with Client("http://127.0.0.1:8790/mcp") as c:
        r = await c.call_tool("list_scenes", {})
        print(r.content[0].text)

asyncio.run(main())
```

```console
demo.spatialdds.org/ut-austin-south-mall: 3 lane(s) of 3 type(s) (announce, content_announce, geo_anchor), 3 readable without a token, from fountain-anchors
```

HTTP mode throttles each client address to five requests a second, in bursts
of up to twenty (`--throttle RATE`; `0` turns it off), and answers past that
with `429` and a `Retry-After` header. Behind a TLS front on the same host, the
client is the first address in `X-Forwarded-For`, and the front's host name
must be allowed with `--allow-host NAME`; without it, a request naming any
host but loopback is refused, which is the MCP SDK's protection against DNS
rebinding.

## What it does not do

- **Write.** No tool publishes, withdraws or commands. Writes wait on the web
  binding's credential flow.
- **Subscribe for you.** MCP notifications and subscriptions are not offered;
  the server's own subscriptions exist so that it can report liveliness.
- **Find scenes near a place.** Scenes come from `--scene`; discovery by place
  is a later version.
- **Decide.** No fusion, no ranking, no trust policy. Two claims are two
  claims.
- **Read with a token.** A lane the binding serves only with one is described
  and not read.
