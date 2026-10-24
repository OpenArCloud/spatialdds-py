"""The one refusal a web-only install meets: the native layer needs its extra."""
from __future__ import annotations

EXTRA = 'pip install "spatialdds[dds]"'


class MissingExtra(ImportError):
    """The native layer was asked for without cyclonedds installed.

    An `ImportError`, because that is what it is, and its own class, because a
    program that degrades to the web client can catch exactly this rather than
    every import failure.
    """


def require_cyclonedds() -> None:
    """Raise `MissingExtra`, naming the extra, unless cyclonedds imports."""
    try:
        import cyclonedds  # noqa: F401
    except ImportError as e:
        raise MissingExtra(
            f"the native layer needs cyclonedds: {EXTRA}") from e
