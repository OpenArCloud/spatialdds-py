# Browser console

Open devtools on any page and paste. There is no SDK, no transport library, and
nothing to install: `fetch` and a `WebSocket` are the whole client.

Public-cacheable resources are served with `Access-Control-Allow-Origin: *`, so
these work from any origin. Operator-scoped resources are not CORS-open and need
a bearer token; see [docs/EVIDENCE.md](../docs/EVIDENCE.md).

A console has no environment, so the endpoint is the first line of each snippet.
The demo endpoint is coming online; `http://127.0.0.1:8787` works today against
a local serve:

```sh
pip install aiohttp mcap
python3 tools/run_gateway.py --mcap samples/fountain-anchors.mcap --port 8787
```

## One fetch, one table

Discovery returns a service manifest; the manifest names the anchor topic's URL;
the URL returns every latched anchor. Three hops, no constructed paths.

<!-- gate: fountain js -->
```js
const BASE = "http://127.0.0.1:8787";
const svc = (await (await fetch(`${BASE}/.well-known/spatialdds/search`)).json()).results
  .map(r => r.service).find(s => s.topics.some(t => t.type === "geo_anchor"));
const url = svc.topics.find(t => t.type === "geo_anchor").url;
const anchors = await (await fetch(url)).json();
console.table(anchors.map(a => ({ id: a.anchor_id, lat: a.geopose.lat_deg, lon: a.geopose.lon_deg, method: a.method })));
```

Devtools renders that as its own sortable table. Under `node`, which is where the
gate runs it, the same snippet prints:

```console
┌─────────┬─────────────────────────────┬─────────────┬──────────────┬─────────────┐
│ (index) │ id                          │ lat         │ lon          │ method      │
├─────────┼─────────────────────────────┼─────────────┼──────────────┼─────────────┤
│ 0       │ 'anchor/fountain-north-rim' │ 30.28498593 │ -97.73935    │ 'Surveyed'  │
│ 1       │ 'anchor/fountain-south-rim' │ 30.28491407 │ -97.73935    │ 'VisualFix' │
│ 2       │ 'anchor/fountain-east-rim'  │ 30.28495    │ -97.73931359 │ 'VisualFix' │
│ 3       │ 'anchor/fountain-west-rim'  │ 30.28495    │ -97.73938641 │ 'VisualFix' │
│ 4       │ 'anchor/fountain-plaque'    │ 30.28496797 │ -97.73932919 │ 'GNSS'      │
└─────────┴─────────────────────────────┴─────────────┴──────────────┴─────────────┘
```

## One WebSocket, until the latched set is complete

The same URL upgrades. A subscriber is replayed the latched set first, then gets
exactly one `end_of_replay` carrying the count, then live samples. That marker is
the contract: before it you are catching up, after it you are current.

<!-- gate: fountain js -->
```js
const url = "ws://127.0.0.1:8787/ut-austin-south-mall/content/content_announce/v1";
const ws = new WebSocket(url);
ws.onmessage = (m) => {
  const e = JSON.parse(m.data);
  if (e.event === "end_of_replay") { console.log(`end_of_replay after ${e.replayed} sample(s)`); ws.close(); }
  else console.log(e.event, e.key, e.data.title ?? "");
};
```

```console
sample content/fountain-marker Littlefield Fountain marker
sample content/fountain-label Littlefield Fountain label
end_of_replay after 2 sample(s)
```

Retirement arrives on the same socket as a terminal sample followed by a
`dispose` on the same key. Nothing is inferred from an absence.
