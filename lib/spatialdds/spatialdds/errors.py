"""One exception family, built from the endpoint's own problem documents.

Appendix N.8 makes every refusal an RFC 7807 `application/problem+json`
document, so the library does not invent an error vocabulary: it carries the
`status`, `type` and `detail` the endpoint sent. The three subclasses exist
only because three failures are so common, and so often a configuration
mistake rather than a bug, that a caller deserves the fix in the message
instead of a status code to look up.
"""
from __future__ import annotations

from typing import Any, Dict


class SpatialDDSError(Exception):
    """Base for everything this library raises."""


class Unreachable(SpatialDDSError):
    """The endpoint did not answer at all."""

    def __init__(self, base: str, cause: BaseException) -> None:
        super().__init__(
            f"{base} is not answering ({type(cause).__name__}). If it should "
            f"be local, serve it: python3 tools/run_gateway.py --mcap "
            f"samples/fountain-anchors.mcap --port 8787")
        self.base, self.cause = base, cause


class Problem(SpatialDDSError):
    """A refusal the endpoint described in a problem document."""

    def __init__(self, status: int, doc: Dict[str, Any], url: str) -> None:
        self.status = status
        self.type = doc.get("type", "")
        self.detail = doc.get("detail", "")
        self.title = doc.get("title", "")
        self.doc, self.url = doc, url
        super().__init__(f"{status} {self.title or self.type}: {self.detail}")

    @property
    def slug(self) -> str:
        """The last segment of the problem type URN."""
        return self.type.rsplit(":", 1)[-1]


class Unauthorized(Problem):
    """401. The write surface is operator-scoped."""

    def __str__(self) -> str:
        return (f"{self.status} {self.detail} Pass token= to connect(); the "
                f"gateway prints the token and its export line at startup.")


class SurfaceOff(Problem):
    """405 from a write verb: publishing is not enabled on this endpoint."""

    def __str__(self) -> str:
        return (f"{self.status} {self.detail} Start the endpoint with "
                f"--publish (and --token) to enable it.")


class IntegrityError(SpatialDDSError):
    """Fetched bytes did not match the hash the manifest declared."""


class UnverifiableIntegrity(SpatialDDSError):
    """The manifest declared an integrity value this library cannot check.

    Raised rather than passed over. SpatialDDS 1.8 requires a `checksum` on
    some records and counts "a checksum field in the body" as a §7.5.2
    integrity signal, but nothing specifies what it digests or in what form
    (spatialdds-web finding 31). Where a declared value does not parse as a
    SHA-256 of the asset's bytes, this library says it cannot verify it
    instead of reporting success it did not establish.
    """


def problem_for(status: int, doc: Dict[str, Any], url: str,
                method: str) -> Problem:
    """The right subclass for a refusal."""
    slug = doc.get("type", "").rsplit(":", 1)[-1]
    if status == 401 or slug == "operator-scoped":
        return Unauthorized(status, doc, url)
    if status == 405 and slug == "publish-disabled":
        return SurfaceOff(status, doc, url)
    return Problem(status, doc, url)
