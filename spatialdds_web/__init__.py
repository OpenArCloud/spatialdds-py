"""The SpatialDDS web binding, served from a recording.

`idlmodel` reads a wire model out of a recording's embedded IDL, `xcdr2` reads
messages with it, `webjson` projects them into the binding's canonical JSON,
`gateway` indexes the result as web resources, and `gw_http` serves the pinned
HTTP and WebSocket surface.
"""
