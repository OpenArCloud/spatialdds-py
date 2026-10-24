"""Who may write where: principals, their scenes, and the table they live in.

One gateway process, **scenes as tenancy**. A scene is the §3.3.1 domain
segment, the first path segment of every resource URL and the second segment
of every `spatialdds://` URI this endpoint holds, so a grant over scenes covers
the collection paths and the resolve surface with one rule.

A bearer token resolves to a **principal**: a name, a provider identity (a
domain-shaped string, recorded on every journal entry the principal writes),
the scenes it may write, and whether it may bring a scene into existence.

**A scene exists** when the endpoint serves it (from the seed or the journal)
or when some grant names it. That is what lets an operator stand up an empty
lane for a party -- name the scene in their grant and it exists, with nothing
in it -- without handing anyone the right to mint scenes. Writing a scene that
exists in neither sense needs `may_create_scenes`, and a principal holding the
wildcard grant `*` without that flag can write every scene that exists and
create none. That closes finding 46's accidental `default` scene at the
gateway: a typo'd or absent scene is a refusal, not a new lane.

**Tokens are never at rest in plaintext.** The table holds the SHA-256 of each
token. The tokens are 256-bit random strings (`issue()` makes them), so a fast
hash is the right hash: there is no low-entropy secret to stretch, and a slow
one would only cost every write a few milliseconds for no attacker it stops.
Comparison is constant-time over every row, so the time a refusal takes says
nothing about which row came close.

**Reads stay open.** Nothing here gates a public read. An operator-scoped
resource (N.4) still wants a credential, and any principal's satisfies it --
access class is about observation versus configuration, not about lanes.

The table file, JSON so it is diffable and needs nothing to parse:

    {"principals": [
      {"name": "james", "provider": "openarcloud.org",
       "token_sha256": "<hex>", "scenes": ["ut-austin-south-mall"],
       "may_create_scenes": false}
    ]}

`python -m spatialdds.tokens` issues, lists and revokes rows; it prints a new
token once and stores only its hash.

**Virtual authorities: designed, not built.** The hook is `AUTHORITIES` below:
an authority name mapped to the set of scenes it answers for, which a request
would select by its `Host` header. Every URI check then compares against the
request's authority instead of the endpoint's one. Nothing reads it yet; it
becomes real when a partner wants their own name over their own lanes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

#: The grant that covers every scene that exists.
ALL = "*"

#: Virtual authorities (designed, not built): authority -> scenes it answers
#: for, selected by the request's Host header. Empty means one authority, the
#: endpoint's own, over every scene -- which is the only shape served today.
AUTHORITIES: dict[str, frozenset[str]] = {}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue() -> str:
    """A new bearer token: 256 bits, URL-safe, never stored by this module."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    name: str
    provider: str
    scenes: frozenset[str]
    may_create_scenes: bool = False

    def grants(self, scene: str) -> bool:
        return ALL in self.scenes or scene in self.scenes

    def as_row(self, token_sha256: str) -> dict:
        return {"name": self.name, "provider": self.provider,
                "token_sha256": token_sha256,
                "scenes": sorted(self.scenes),
                "may_create_scenes": self.may_create_scenes}


@dataclass
class TokenTable:
    """Token digests -> principals. Loaded from a file, or one legacy token."""

    rows: list[tuple[str, Principal]] = field(default_factory=list)
    path: Optional[Path] = None

    @classmethod
    def single(cls, token: Optional[str]) -> "TokenTable":
        """The pre-tenancy shape: one `--token`, every scene, may create.

        Kept so that `--token` means exactly what it meant -- one operator
        who may write anything -- and every gate and README line written
        against it is unaffected.
        """
        if not token:
            return cls([])
        return cls([(digest(token), Principal(
            "operator", "", frozenset({ALL}), may_create_scenes=True))])

    @classmethod
    def load(cls, path: Path) -> "TokenTable":
        doc = json.loads(Path(path).read_text())
        rows = []
        seen: set[str] = set()
        for r in doc.get("principals", []):
            h = str(r["token_sha256"]).lower()
            if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
                raise ValueError(f"principal {r.get('name')!r}: token_sha256 "
                                 f"is not a SHA-256 hex digest")
            if h in seen:
                raise ValueError(f"principal {r.get('name')!r}: a token digest "
                                 f"appears twice, so one token would be two "
                                 f"principals")
            seen.add(h)
            scenes = r.get("scenes", [])
            if isinstance(scenes, str) or not scenes:
                raise ValueError(f"principal {r.get('name')!r}: scenes must be "
                                 f"a non-empty list (use [\"*\"] for every "
                                 f"existing scene)")
            rows.append((h, Principal(str(r["name"]), str(r.get("provider", "")),
                                      frozenset(map(str, scenes)),
                                      bool(r.get("may_create_scenes", False)))))
        names = [p.name for _h, p in rows]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"principal names repeat: {', '.join(dup)}; the "
                             f"name is what the journal records")
        return cls(rows, Path(path))

    def reload(self) -> "TokenTable":
        """Re-read the file in place; a bad file leaves the old table."""
        if self.path is None:
            return self
        fresh = TokenTable.load(self.path)
        self.rows = fresh.rows
        return self

    def save(self) -> None:
        assert self.path is not None
        doc = {"principals": [p.as_row(h) for h, p in self.rows]}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   suffix=".json.tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def __bool__(self) -> bool:
        return bool(self.rows)

    def authenticate(self, header: str) -> Optional[Principal]:
        """The principal a `Authorization: Bearer ...` header names, or None.

        Every row is compared, matched or not, so the work done does not
        depend on where (or whether) the token is in the table.
        """
        if not header.startswith("Bearer "):
            return None
        h = digest(header[7:])
        found = None
        for row_h, p in self.rows:
            if hmac.compare_digest(row_h, h):
                found = p
        return found

    def granted_scenes(self) -> set[str]:
        """Every scene some grant names explicitly: lanes that exist empty."""
        return {s for _h, p in self.rows for s in p.scenes if s != ALL}

    def principals(self) -> Iterable[Principal]:
        return (p for _h, p in self.rows)


def scene_exists(scene: str, served: Iterable[str], table: TokenTable) -> bool:
    return scene in set(served) or scene in table.granted_scenes()


def check_write(p: Principal, scene: str, served: Iterable[str],
                table: TokenTable) -> Optional[tuple[int, str, str, str]]:
    """None if `p` may write `scene`, else (status, slug, title, detail)."""
    if not p.grants(scene):
        lanes = ", ".join(sorted(p.scenes)) or "none"
        return (403, "not-your-lane", "Not your lane",
                f"The token for {p.name!r} may write the scene(s) {lanes}, and "
                f"this write is to scene {scene!r}. Reads of every scene stay "
                f"open; writes are per lane.")
    if not scene_exists(scene, served, table) and not p.may_create_scenes:
        return (403, "scene-creation-not-granted", "No such scene",
                f"Scene {scene!r} does not exist on this endpoint, and the "
                f"token for {p.name!r} may not create scenes. Check the "
                f"scene name; an operator grants new lanes.")
    return None
