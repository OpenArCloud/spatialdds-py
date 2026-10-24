"""The limits a public registry states and enforces.

Every limit refuses with an N.8 problem document that names it, so a client
learns which limit it hit and what the number is, never a framework's bare
status page:

| limit | default | refusal |
| --- | --- | --- |
| request body | 256 KiB | `413 body-too-large` |
| request target (path and query) | 16 KiB | `414 uri-too-long` |
| records per scene, per principal (token table `max_records`) | unlimited | `403 quota-exceeded` |
| concurrent WebSocket subscriptions | 256 | `503 too-many-subscriptions` |
| requests per client IP | 20/s, burst 60 | `429 rate-limited`, with `Retry-After` |

The client IP is the socket's peer, except that a peer on loopback is a
reverse proxy (Caddy, in the deployed shape) and its `X-Forwarded-For` names
the client. A header from anywhere else is ignored: honouring it would let any
caller choose the bucket it is counted in. A loopback peer with no forwarded
address is the operator on the box (or a gate), and is not rate-limited:
behind Caddy every outside request arrives forwarded.

Per-IP rate limiting was ruled for the TLS front. The Caddy the deploy
installs (Ubuntu's 2.6.2) has no rate-limit directive without a third-party
plugin, so it lives here instead, keyed on the address Caddy forwards; Caddy
keeps the body limit, set above this one so this endpoint's problem document
is what a client sees.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

#: slug -> (status, title, when). Documented by tools/gen_openapi.py.
REFUSALS: dict[str, tuple[int, str, str]] = {
    "body-too-large": (413, "Request body too large",
                       "a request body over the endpoint's stated limit"),
    "uri-too-long": (414, "Request target too long",
                     "a path and query over the endpoint's stated limit"),
    "unsupported-media-type": (415, "Body is not JSON by its content type",
                               "a request body sent as anything but "
                               "application/json"),
    "rate-limited": (429, "Too many requests",
                     "more requests from one client than its rate allows"),
    "too-many-subscriptions": (503, "Too many subscriptions",
                               "the endpoint's WebSocket cap is reached"),
}


#: The HTTP parser's own line and header limits, set well above `max_url` so
#: that a long target reaches the application and is refused there as a
#: problem document. At aiohttp's default (8190) a 9 kB URI was refused by the
#: parser itself as plain text, with a traceback in the log (found by
#: gates/fuzz.py). In the deployed shape Caddy's `max_header_size` sits between
#: the two, so nothing past it reaches this process at all.
HANDLER_ARGS = {"max_line_size": 65536, "max_field_size": 65536}


@dataclass
class Limits:
    max_body: int = 256 * 1024
    max_url: int = 16 * 1024
    max_ws: int = 256
    rate_per_s: float = 20.0
    burst: int = 60
    #: client -> (tokens, last refill time)
    _buckets: dict = field(default_factory=dict, repr=False)
    ws_open: int = 0

    def client(self, peer: str | None, forwarded: str | None) -> str | None:
        """The bucket a request counts against; None for the local operator."""
        if peer in ("127.0.0.1", "::1"):
            return forwarded.split(",")[-1].strip() if forwarded else None
        return peer or ""

    def take(self, client: str, now: float | None = None) -> float:
        """0 if the request may proceed, else seconds until it could."""
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.get(client, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate_per_s)
        if tokens >= 1.0:
            self._buckets[client] = (tokens - 1.0, now)
            if len(self._buckets) > 10000:
                self._evict(now)
            return 0.0
        self._buckets[client] = (tokens, now)
        return (1.0 - tokens) / self.rate_per_s

    def _evict(self, now: float) -> None:
        """Forget clients whose bucket has refilled; bounded memory."""
        full = self.burst / self.rate_per_s
        for c, (_t, last) in list(self._buckets.items()):
            if now - last > full:
                del self._buckets[c]
