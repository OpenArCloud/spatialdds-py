"""The reference gateway: the SpatialDDS web binding, served.

`idlmodel` reads a wire model out of IDL, `xcdr2` reads and writes messages
with it, `webjson` projects them into the binding's canonical JSON and lifts
them back, `gateway` indexes the result as web resources, `publish` is the
implementation-defined write surface, and `gw_http` serves the pinned HTTP and
WebSocket surface. `spatialdds.serve` is the command that stands it up.

**This half does not import the client half.** The client is `requests` and
`websockets` talking to an endpoint over the wire; the server is `aiohttp` and
`mcap` being that endpoint. They share the vendored artifacts under
`artifacts/` -- as data, read by both -- and nothing else. The `spatialdds`
package's own `__init__` is deliberately free of third-party imports so that
importing `spatialdds.server` does not drag the client's dependencies in
behind it; `gates/import_graph.py` holds both directions.

It lives inside the distribution rather than beside it so that
`pip install "spatialdds[server]"` is enough to stand up a registry, with no
clone and no second copy of the gateway anywhere. The repository's
`tools/run_gateway.py` is a shim over this.
"""
