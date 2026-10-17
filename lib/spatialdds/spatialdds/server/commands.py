"""N.6 commands: the gateway owns the HTTP leg, a backend supplies the verbs.

Appendix N.6 is four sentences long and delegates its conventions to M.1, so
most of this module is the reasoning that fills the gap between them. The
ruling this implements: **N.6 is a gateway surface whose semantics a backend
supplies.** The gateway owns the route, the auth, the validation and the
response shape; a backend registers a handler per command type and decides
what a verb means. An endpoint whose backend registers nothing stays
northbound-only, and its POST stays exactly as conformant as it was before
this module existed.

**Why there is a seam at all.** The first consumer of this surface is
`SpatialDDS-demo`'s web bridge, which embeds the gateway against a live DDS
bus: a `goto` arriving as a POST has to leave as a keyed `ModelCommand` on a
DDS topic, and nothing in this repository knows how to do that or should. So
the deliverable is not only the route but the boundary — the narrowest thing
an embedder can implement to own the verbs without owning the HTTP.

**What N.6 fixes and what it leaves open.** It fixes that a command is a POST,
that the response carries the outcome *including an explicit decline*, and
that an endpoint without a command lane refuses with `405` or `404` and never
accepts a command it cannot act on. It says nothing about status codes for the
outcomes themselves, nothing about how a client discovers that commands are
accepted, and nothing about completion tracking. Those are decided here and
documented in `docs/WIRE.md`; none of them is invented where the text speaks.

**M.1's vocabulary, which N.6 points at.** A decline is "a first-class,
observable outcome, not an absence", carrying a terminal state and a
human-readable reason. The value of the pattern is that an observer can
distinguish *refused* from *not-yet-answered* from *not-for-me*. That is why
`Outcome` carries a reason on the decline path and why a declined command is
not reported with a success status: a caller that checks only the status must
not read a refusal as an acceptance, which would turn M.1's first-class
outcome back into the absence it exists to replace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

#: The manifest member that advertises a lane as accepting commands.
#:
#: **Implementation-defined, and flagged as a specification gap.** N.7's
#: topic-entry table is a MUST-carry list of six fields plus two optional
#: ones, and none of them can say "this lane accepts commands" --- while N.6
#: says "where an endpoint exposes a command surface" and gives a client no
#: way to find out whether it does. A client would otherwise have to POST and
#: read the refusal, which is discovery by trial.
#:
#: So this member exists, under this repository's own name, documented in
#: `docs/WIRE.md`, and filed for 1.9 rather than treated as settled. N.7's
#: list is a floor rather than a ceiling, so carrying an extra member is
#: conformant; a *client* may not depend on it until the specification adopts
#: one, and nothing in this repository reads it except the manifest's own
#: reader and the conformance suite.
ADVERTISEMENT = "accepts_commands"


@dataclass(frozen=True)
class Outcome:
    """What a backend did with a command.

    Two states and a reason, which is M.1's vocabulary and not a richer one:
    there is no `PENDING` because N.6 requires no completion tracking and
    inventing it would put this gateway ahead of the specification it
    implements. A backend that needs asynchronous completion reports
    acceptance here and publishes the completion on its own lane, which is
    what the DDS side already does.
    """

    accepted: bool
    #: Required on a decline, and M.1 is why: "a decline is a first-class,
    #: observable outcome, not an absence." A refusal with no reason is an
    #: absence with a status code.
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.accepted and not self.reason:
            raise ValueError(
                "a declined command must carry a reason: M.1 makes a decline "
                "a first-class outcome, and a refusal nobody can read is the "
                "absence it exists to replace")

    @classmethod
    def accept(cls, reason: str = "") -> "Outcome":
        return cls(True, reason)

    @classmethod
    def decline(cls, reason: str) -> "Outcome":
        return cls(False, reason)


#: A handler receives the validated, lifted record --- the same JSON shape a
#: GET of that type would serve, after N.1 canonicalization and schema
#: validation --- and returns an `Outcome`. It never sees the request, the
#: token or the socket: an embedder owns verbs, not HTTP.
Handler = Callable[[dict[str, Any]], Outcome]


class Registry:
    """The handlers an embedder has registered, keyed by type FQN.

    Keyed by FQN rather than by topic so that a backend which accepts
    `spatial.owm.ModelCommand` accepts it wherever a scene advertises that
    type, which is how every other type-keyed table in this package works
    (`DURABILITY`, `PUBLISHABLE`, the schema index).

    Empty is the normal state. A gateway serving a recording has no verbs to
    offer, and N.6 says a conformance suite MUST NOT require a command
    surface to exist.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, fqn: str, handler: Handler) -> None:
        """Take commands of one type. Re-registering replaces, deliberately:
        an embedder reconfiguring a lane should not have to tear down."""
        self._handlers[fqn] = handler

    def accepts(self, fqn: str) -> bool:
        return fqn in self._handlers

    def handler(self, fqn: str) -> Handler | None:
        return self._handlers.get(fqn)

    def types(self) -> list[str]:
        return sorted(self._handlers)

    def __bool__(self) -> bool:
        return bool(self._handlers)
