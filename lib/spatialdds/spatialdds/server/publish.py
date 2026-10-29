"""The write surface. Implementation-defined, labeled, and off by default.

SpatialDDS 1.8 specifies no publish verb and forbids none. Everything here is
an endpoint extension: a read-only client cannot tell a published instance from
a replayed one, and the conformance suite never touches these routes, so the
endpoint stays conformant with the surface enabled or disabled. Nothing in here
is proposed as specification text by erratum -- an erratum fixes a
contradiction and this is a feature. It is written up as 1.9 candidate entry 4.

Three rules carry the design.

**A published instance is canonicalized by construction, not by promise.** An
accepted body is encoded to XCDR2 with the same `Writer` validated
byte-identically against CycloneDDS, then decoded and projected back through
the same `Decoder` and `Projector` the serving path uses for a recording. What
is stored is the output of that round trip. So "indistinguishable from a
replayed instance" is not a property this module maintains; it is the only
representation it can produce.

**The journal is a recording.** Accepted writes append to an MCAP carrying the
same embedded IDL the seed carried, byte for byte, so a backup is readable by
any SpatialDDS tool rather than only by this one.

**Identity should come from the credential, not the body.** This batch does not
yet honour that -- `provider_id` and `org` are read from the request, and the
server sets no fields -- and the gap is recorded rather than hidden. The OSCP
census's platform-set SCR fields are the precedent; 1.9 candidate entry 4
carries it as a design rule.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcap.reader import make_reader
from mcap.writer import Writer as McapWriter

from . import gateway, mcapmeta, webjson, xcdr2

# Exactly two publishable record kinds, the SSD and SCD successors. The type
# segment of the resource path decides the type, so this table is also what
# refuses a PUT to any other topic: anchors, zones and the rest stay read-only.
PUBLISHABLE: dict[str, str] = {
    "announce": "spatial.disco.Announce",
    "content_announce": "spatial.disco.ContentAnnounce",
    # Anchors joined in the batch after the 1.8 revision. Mechanically this is
    # a third row: the same routes, token, validation, journal, events and
    # If-Match. What is new is what the record means -- a published anchor
    # asserts a pose at the confidence its own `method` and `cov` declare, and
    # publishing does not upgrade that claim. See docs/PUBLISH.md.
    "geo_anchor": "spatial.core.GeoAnchor",
    # The world model's entity lane, 0.8.0: an HTTP client can put things
    # into a world, not only records about one. Its basis decides whether it
    # has to keep saying so (`lease_for`). See docs/PUBLISH.md.
    "entity": "spatial.owm.Entity",
}

#: Every row above is a record lane except these: a world-model lane, whose
#: type carries a basis and a lifecycle, so its basis gates liveliness and
#: its withdrawal is a terminal sample before the dispose. A deployment's own
#: keyed type (`State.publishable`) is decided by its members instead.
WORLD_LANES = frozenset({"spatial.owm.Entity"})

# A dispose has no 1.8 record. `Depart` is a service withdrawal and nothing
# expresses a content withdrawal, so the journal carries disposes as MCAP
# metadata records under this name rather than as an invented typed message.
# Labeled, and recorded as a gap in the candidates entry. Defined in `gateway`
# since 0.7.0, because the recording loader applies them too.
DISPOSE_RECORD = mcapmeta.DISPOSE_RECORD
#: A published content manifest, journalled as a metadata record.
#:
#: N.7 manifests are JSON documents and not IDL types, so there is no schema
#: to register and no channel to write them on; MCAP metadata is where a
#: recording keeps things that are about the data rather than in it, which is
#: also where disposes live. Found by restarting a registry that had been
#: published into: the three announces came back and `resolve` answered 404,
#: because the manifest had only ever been in memory. Before 0.2.0 manifests
#: arrived from `--manifests` on disk and survived by being files; once an
#: empty registry is the normal way to start, "everything still served after a
#: restart" has to include them.
MANIFEST_RECORD = mcapmeta.MANIFEST_RECORD
#: A manifest withdrawn over `DELETE /spatialdds/resolve` (finding 44).
MANIFEST_WITHDRAW_RECORD = mcapmeta.MANIFEST_WITHDRAW_RECORD
#: Who wrote a journalled sample, and into which scene. A sample is an MCAP
#: message, which has no room for attribution of its own, so it rides beside
#: the message as a metadata record keyed by the same sequence number.
#: Disposes and manifests carry the same two fields in their own records.
#: A journal written before tenancy has none of these, and replays with an
#: empty principal: attribution unknown, which is the truth about it.
WRITTEN_BY_RECORD = mcapmeta.WRITTEN_BY_RECORD


class JournalUnwritable(Exception):
    """The journal could not be written; nothing was accepted."""


class Rejected(Exception):
    """A refusal with the problem+json a caller should receive."""

    def __init__(self, status: int, slug: str, title: str, detail: str,
                 **extra: Any) -> None:
        super().__init__(detail)
        self.status, self.slug, self.title, self.detail = (status, slug, title,
                                                           detail)
        self.extra = extra


def uris_in(doc: Any) -> list[str]:
    """Every `spatialdds://` URI the record carries, at any depth."""
    out: list[str] = []
    if isinstance(doc, dict):
        for v in doc.values():
            out.extend(uris_in(v))
    elif isinstance(doc, list):
        for v in doc:
            out.extend(uris_in(v))
    elif isinstance(doc, str) and doc.startswith(gateway.SCHEME):
        out.append(doc)
    return out


def check_authority(base: str, doc: Any) -> None:
    """Every URI in the record must belong to the authority this endpoint is.

    The authority comes from the same `--public-url` the resolver uses, so an
    endpoint cannot accept records into a namespace it does not answer for.
    With no authority there is nothing to check against, and a check with
    nothing to check must not pass: the surface refuses instead.
    """
    mine = gateway.base_authority(base)
    if not mine:
        raise Rejected(
            403, "no-authority", "This endpoint has no authority",
            "The publish surface accepts records only into the authority this "
            "endpoint answers for, and this endpoint was started without one. "
            "Pass --public-url. An authority check with nothing to check "
            "against would pass every record, which is worse than refusing.")
    foreign = sorted({u for u in uris_in(doc)
                      if gateway.uri_authority(u) != mine})
    if foreign:
        raise Rejected(
            403, "foreign-authority", "Another authority's URI",
            f"This endpoint is the authority for {mine!r} and will not accept "
            f"a record carrying URIs under another. Offending: "
            f"{', '.join(foreign)}.", authority=mine, foreign=foreign)


def validate_schema(fqn: str, doc: Any, st: "gateway.State | None" = None) -> None:
    """The vendored generated schema, checked with the gates' own checker.

    A deployment's own type has no vendored schema; it is checked against the
    one this endpoint publishes for it under N.2, written from the same model
    by the specification generator's rules (`webjson.schema_from_model`).
    """
    try:
        import jsonschema
    except ImportError as e:                                  # pragma: no cover
        raise Rejected(500, "no-validator", "Cannot validate",
                       f"The publish surface validates every body against the "
                       f"vendored schemas and jsonschema is not installed: "
                       f"{e}") from e
    schema = webjson.schemas().get(fqn)
    if schema is None and st is not None and st.model is not None \
            and fqn in st.publishable.values():
        schema = webjson.schema_from_model(st.model, fqn)
    if schema is None:
        raise Rejected(500, "no-schema", "No schema for that type",
                       f"No vendored schema for {fqn}, so a body cannot be "
                       f"validated and will not be accepted.")
    try:
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise Rejected(
            400, "schema-invalid", "Body does not match its schema",
            f"{fqn} at {where}: {e.message}. Every publishable type requires "
            f"every member, so a body is a whole record and never a patch.",
            fqn=fqn, at=where) from e


def canonicalize(st: gateway.State, fqn: str,
                 doc: dict) -> tuple[dict, bytes]:
    """Round-trip the body through the wire, and keep what comes back."""
    if st.model is None:
        raise Rejected(503, "no-wire-model", "No wire model",
                       "This endpoint learned no types, so it cannot encode a "
                       "record. Seed it with --mcap or a journal.")
    try:
        wire = webjson.Lifter(st.model, webjson.tables()).lift(fqn, doc)
        raw = xcdr2.Writer(st.model).encode(fqn, wire)
        back = xcdr2.Decoder(st.model).decode(fqn, raw)
        tables = webjson.tables()
        data = webjson.Projector(st.model, tables).project(fqn, back)
    except Exception as e:
        raise Rejected(
            400, "not-encodable", "Body cannot be put on the wire",
            f"{fqn} passed schema validation but could not be encoded as "
            f"XCDR2 and read back: {type(e).__name__}: {e}") from e
    return data, raw


def resource_for_put(st: gateway.State, scene: str, stream: str,
                     type_seg: str, version: str) -> tuple[str, gateway.Resource]:
    """The resource a PUT addresses, created if this topic is new."""
    fqn = PUBLISHABLE.get(type_seg) or st.publishable.get(type_seg)
    every = sorted(set(PUBLISHABLE) | set(st.publishable))
    if fqn is None:
        raise Rejected(
            405, "not-publishable", "That topic is not publishable",
            f"This endpoint publishes {', '.join(every)}, and the "
            f"deployment's own keyed types once registered (`extend()`). "
            f"Everything else it serves is read-only.", publishable=every)
    if version != "v1":
        raise Rejected(404, "no-such-version", "No such version",
                       f"Version {version!r} is not served.")
    if not scene:
        # A four-segment topic is served read-only in the well-known namespace:
        # it has no scene, so no lane can grant a write to it.
        raise Rejected(405, "not-publishable", "That topic is not publishable",
                       "A topic with no scene segment is served read-only; "
                       "this endpoint's write surface is per scene.",
                       publishable=every)
    topic = f"spatialdds/{scene}/{stream}/{type_seg}/{version}"
    res = st.resources.get(gateway.resource_path(scene, stream, type_seg, version))
    if res is None:
        agg = st.model.get(fqn) if st.model else None
        if agg is None:
            raise Rejected(503, "unknown-type", "Type not known here",
                           f"This endpoint learned no definition for {fqn}, so "
                           f"it cannot serve it. Seed it with a recording or "
                           f"journal that carries the type.")
        keys = [m.name for m in agg.members if m.is_key]
        # A deployment's keyed type, published into, is state a late joiner
        # is handed: latched. Who may read it is still the deployment's
        # call, by the same `--public` rule a recorded lane follows.
        latched, access = gateway.DURABILITY.get(
            fqn, (fqn in st.publishable.values(), gateway.OPERATOR))
        if fqn in st.public:
            access = gateway.PUBLIC
        res = gateway.Resource(scene, stream, type_seg, version, topic, fqn,
                               keys[0] if keys else "", access, latched)
        st.resources[res.path] = res
        st.latched.setdefault(topic, {})
        st.latched_initial.setdefault(topic, {})
        st.live.setdefault(topic, [])
        st.scenes = sorted(set(st.scenes) | {scene})
    return topic, res


def apply_sample(st: gateway.State, topic: str, res: gateway.Resource,
                 key: str, data: dict) -> bool:
    """Put an instance into the latched set. True if it is new.

    This is half of what a live backend needs, and the half that is easy to
    mistake for all of it: it decides what a *new* subscriber replays and what
    the collection GET returns, and it reaches nobody already on a socket.
    `gw_http.notify(app, topic, event)` is the other half. An embedder that
    calls only this one has a registry that is correct for every client except
    the ones currently watching.
    """
    created = key not in st.latched.get(topic, {})
    st.latched.setdefault(topic, {})[key] = gateway.Instance(
        key, data, webjson.digest(data))
    # A published instance is latched state a later subscriber must replay,
    # exactly like a definition the recording carried.
    st.latched_initial.setdefault(topic, {})[key] = st.latched[topic][key]
    st.counts[topic] = st.counts.get(topic, 0) + 1
    return created


def apply_dispose(st: gateway.State, topic: str, key: str) -> bool:
    """Remove an instance. True if it was there."""
    had = key in st.latched.get(topic, {})
    st.latched.get(topic, {}).pop(key, None)
    st.latched_initial.get(topic, {}).pop(key, None)
    return had


# ---- liveliness, by basis ---------------------------------------------------
#
# A DDS writer's liveliness says whether anybody is still asserting what it
# wrote. An HTTP client has none: a request ends and says nothing about the
# next one. So the write surface cannot fake liveliness, and does not try.
# What it does instead is ruled by basis:
#
# * DECLARED and AUTHORED are declarations. They hold until withdrawn, like
#   any record, and have no liveliness to fake.
# * OBSERVED and DERIVED say somebody is watching. The PUT declares how
#   often it will say so again (`?refresh=` seconds); a re-PUT inside that
#   interval keeps it; when it lapses the gateway withdraws it itself, with a
#   terminal sample and the reason "refresh lapsed". The gateway owns the
#   writer, so that is a deliberate removal by the writer, as §2.14 has it,
#   not a disposal synthesized on somebody else's behalf.

#: The bases that say somebody is watching, and so must keep saying it.
LIVE_BASES = frozenset({"OBSERVED", "DERIVED"})
#: The refresh interval a PUT may declare, in seconds. A second is the
#: shortest a client over HTTP can honestly promise; an hour is long enough
#: that "observed" has stopped meaning anything.
REFRESH_MIN_S, REFRESH_MAX_S = 1.0, 3600.0
#: The reason on a withdrawal the gateway made because nobody refreshed.
LAPSED = "refresh lapsed"
#: The members a type needs for its basis to gate it and its withdrawal to
#: be a terminal sample: the world model's lifecycle, as Entity spells it.
WORLD_MEMBERS = frozenset({"basis", "state", "state_reason", "stamp"})


@dataclass
class Lease:
    """An observed or derived instance's promise to be refreshed."""
    interval: float
    #: `time.monotonic()` at which it lapses.
    deadline: float

    @classmethod
    def starting(cls, interval: float) -> "Lease":
        return cls(interval, time.monotonic() + interval)


def members_of(st: gateway.State, fqn: str) -> set[str]:
    agg = st.model.get(fqn) if st.model else None
    return {m.name for m in agg.members} if agg is not None else set()


def is_world(st: gateway.State, fqn: str) -> bool:
    """Whether a type's basis gates it: the entity lane, or a deployment's
    own keyed type spelled with the same lifecycle members (a claim)."""
    if fqn in WORLD_LANES:
        return True
    return fqn in st.publishable.values() and \
        WORLD_MEMBERS <= members_of(st, fqn)


def lease_for(st: gateway.State, fqn: str, doc: dict,
              refresh: str | None) -> float | None:
    """The refresh interval this write promises, or None for a declaration.

    Refuses an observed or derived write with no interval, a declaration with
    one, and an interval out of range, each naming the rule.
    """
    if not is_world(st, fqn):
        if refresh is not None:
            raise Rejected(
                400, "refresh-not-applicable", "This lane has no refresh",
                f"{fqn} is a record, not a claim about the world: it has no "
                f"basis, holds until withdrawn, and takes no ?refresh=.",
                rule="basis")
        return None
    basis = str(doc.get("basis", ""))
    if basis not in LIVE_BASES:
        if refresh is not None:
            raise Rejected(
                400, "refresh-not-applicable", "A declaration has no refresh",
                f"basis {basis} is a declaration: it holds until withdrawn, "
                f"has no liveliness to keep, and takes no ?refresh=. Only "
                f"{' and '.join(sorted(LIVE_BASES))} say somebody is "
                f"watching, and only they must keep saying it.",
                rule="basis", basis=basis)
        return None
    if refresh is None:
        raise Rejected(
            400, "refresh-required", "An observed claim needs a refresh interval",
            f"basis {basis} says somebody is watching, and over HTTP nothing "
            f"else can say whether they still are. Declare how often you will "
            f"re-PUT it with ?refresh=<seconds> ({REFRESH_MIN_S:g} to "
            f"{REFRESH_MAX_S:g}); if a refresh does not arrive in time this "
            f"endpoint withdraws it with the reason {LAPSED!r}. A DECLARED or "
            f"AUTHORED body needs none.", rule="basis", basis=basis)
    try:
        interval = float(refresh)
    except ValueError:
        interval = float("nan")
    if not (REFRESH_MIN_S <= interval <= REFRESH_MAX_S):
        raise Rejected(
            400, "refresh-invalid", "Refresh interval out of range",
            f"?refresh={refresh!r} is not a number of seconds from "
            f"{REFRESH_MIN_S:g} to {REFRESH_MAX_S:g}.", rule="basis",
            refresh=refresh)
    return interval


#: The member a claim names its asserter in (the 0.2 design's
#: Relationship, and any deployment type spelled the same way).
ASSERTER = "asserter"


def ground_asserter(st: gateway.State, fqn: str, doc: Any,
                    principal: str) -> Any:
    """A claim's asserter is the credential's principal, stamped or verified.

    A bus claim's asserter is grounded in the participant that wrote it; an
    HTTP claim's is grounded in the bearer token, the same way. An empty
    asserter is stamped with the principal's name; any other value must be
    that name. An endpoint with no token table has no principal, so it has
    nothing to ground a claim in and refuses one rather than accept an
    asserter nobody vouched for.
    """
    if ASSERTER not in members_of(st, fqn) or not isinstance(doc, dict):
        return doc
    claimed = doc.get(ASSERTER, "")
    if not principal:
        raise Rejected(
            403, "asserter-ungrounded", "No principal to assert this claim",
            f"A {fqn} names its asserter, and over HTTP the asserter is the "
            f"bearer token's principal. This endpoint has no token table "
            f"(--tokens), so no request carries a principal, and a claim "
            f"cannot be grounded in one.", rule="asserter")
    if claimed in ("", None):
        return {**doc, ASSERTER: principal}
    if claimed != principal:
        raise Rejected(
            403, "asserter-not-principal", "You may only assert as yourself",
            f"The body's asserter is {claimed!r} and this token's principal "
            f"is {principal!r}. A claim's asserter is who is making it, and "
            f"over HTTP that is the credential's principal: leave asserter "
            f"empty and it is stamped, or set it to {principal!r}.",
            rule="asserter", asserter=claimed, principal=principal)
    return doc


def terminal(st: gateway.State, fqn: str, data: dict,
             reason: str) -> tuple[dict, bytes]:
    """The instance as its last sample says it is, retired, with the reason.

    What the SDK's `Writer.withdraw()` writes on the bus, written here for
    HTTP: the last sample with `state` RETIRED, `state_reason` the reason and
    `stamp` now, canonicalized by the same round trip as any accepted body.
    """
    doc = {**data, "state": "RETIRED", "state_reason": reason,
           "stamp": now_stamp()}
    return canonicalize(st, fqn, doc)


# ---- the journal -----------------------------------------------------------

class Journal:
    """Accepted writes and disposes, as an MCAP any SpatialDDS tool can read.

    Rewritten whole and renamed into place on every accepted write. The MCAP
    format puts its summary at the end, so appending in place means leaving a
    file that is not a complete MCAP until the process exits cleanly -- and a
    journal that is only valid after a graceful shutdown is not a backup. A
    registry of this size makes the rewrite cheaper than an append-and-repair
    scheme, and the file on disk is a finished, summarised recording at every
    instant.
    """

    def __init__(self, path: Path, st: gateway.State) -> None:
        self.path, self.state = path, st
        self.entries: list[dict] = []
        self.seq = 0

    # -- reading --
    def replay(self) -> int:
        """Apply the journal to the state. Returns entries applied."""
        if not self.path.is_file():
            return 0
        if self.state.model is None:
            # The journal embeds the same IDL the seed did, which is the whole
            # reason to write it that way: a gateway started with a journal and
            # no recording learns its types from the journal itself.
            model, idl = gateway._wire_model(self.path)
            self.state.model, self.state.idl = model, idl
        dec = xcdr2.Decoder(self.state.model)
        proj = webjson.Projector(self.state.model, webjson.tables())
        items, _idl = read_entries(self.path)
        for seq, e in items:
            self.seq = max(self.seq, seq)
            if e["kind"] == "manifest-withdraw":
                self.state.manifests.pop(e["uri"], None)
                self.entries.append({**e, "seq": seq})
                continue
            if e["kind"] == "manifest":
                # Last one wins, which is what replaying in sequence means: a
                # manifest republished under the same URI replaced the earlier
                # one when it was accepted, and replay reaches the same state.
                self.state.manifests[e["uri"]] = json.loads(e["doc"])
                self.entries.append({**e, "seq": seq})
                continue
            seg = gateway.split_topic(e["topic"])
            if seg is None:
                continue
            if e["kind"] == "sample":
                topic, res = resource_for_put(self.state, *seg)
                data = proj.project(e["fqn"], dec.decode(e["fqn"], e["raw"]))
                key = str(data.get(res.key, "")) if res.key else ""
                apply_sample(self.state, topic, res, key, data)
                # A lease restarts from now: the gateway was down, and a
                # client cannot be held to a deadline that passed while
                # nobody was there to refresh to. It still has to refresh
                # within its own interval, or it lapses as it would have.
                if e.get("refresh"):
                    self.state.leases[(topic, key)] = Lease.starting(e["refresh"])
                else:
                    self.state.leases.pop((topic, key), None)
                self.entries.append({**e, "seq": seq, "key": key})
            else:
                apply_dispose(self.state, e["topic"], e["key"])
                self.state.leases.pop((e["topic"], e["key"]), None)
                self.entries.append({**e, "seq": seq})
        return len(self.entries)

    # -- writing --
    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def record_sample(self, topic: str, fqn: str, key: str,
                      raw: bytes, principal: str = "",
                      scene: str = "", refresh: float | None = None) -> None:
        """An accepted sample; `refresh` the interval an observed one promised,
        so a restarted gateway still withdraws it if nobody keeps it."""
        self.entries.append({"kind": "sample", "seq": self._next(),
                             "topic": topic, "fqn": fqn, "key": key,
                             "raw": raw, "principal": principal,
                             "scene": scene or _topic_scene(topic),
                             "refresh": refresh})
        self._flush()

    def record_manifest(self, uri: str, doc: dict, principal: str = "",
                        scene: str = "") -> None:
        """A published manifest, stored as the canonical JSON that was served.

        `webjson.canon` and not `json.dumps`: the bytes written here are the
        bytes the endpoint serves for that URI, so a journalled manifest and a
        served one cannot differ by key order or spacing.
        """
        self.entries.append({"kind": "manifest", "seq": self._next(),
                             "uri": uri, "doc": webjson.canon(doc),
                             "principal": principal,
                             "scene": scene or gateway.uri_scene(uri)})
        self._flush()

    def record_manifest_withdraw(self, uri: str, reason: str,
                                 principal: str = "",
                                 scene: str = "") -> None:
        self.entries.append({"kind": "manifest-withdraw", "seq": self._next(),
                             "uri": uri, "reason": reason,
                             "principal": principal,
                             "scene": scene or gateway.uri_scene(uri)})
        self._flush()

    def record_dispose(self, topic: str, key: str, reason: str,
                       principal: str = "", scene: str = "") -> None:
        self.entries.append({"kind": "dispose", "seq": self._next(),
                             "topic": topic, "key": key, "reason": reason,
                             "principal": principal,
                             "scene": scene or _topic_scene(topic)})
        self._flush()

    def _flush(self) -> None:
        """Write the journal, or undo the entry that would not fit.

        Called by every `record_*` *before* the state changes, so a write the
        disk refuses (full, read-only, gone) is refused to the client as well
        and the registry never holds what its journal does not. The temp file
        is removed on failure, and the previous journal is untouched because
        it is only ever replaced by rename.
        """
        try:
            write_entries(self.path, self.entries, self.state.idl)
        except OSError as e:
            self.entries.pop()
            self.seq -= 1
            raise JournalUnwritable(f"{type(e).__name__}: {e}") from e


def _topic_scene(topic: str) -> str:
    seg = gateway.split_topic(topic)
    return seg[0] if seg else ""


def read_entries(path: Path) -> tuple[list[tuple[int, dict]], dict]:
    """Every journal entry in sequence order, with its attribution, unapplied.

    Also returns the embedded IDL by type, so an offline tool can write the
    entries back without a gateway or a specification checkout.
    """
    items: list[tuple[int, dict]] = []
    who: dict[int, tuple[str, str, float | None]] = {}
    idl: dict[str, tuple[str, bytes]] = {}
    with open(path, "rb") as f:
        r = make_reader(f)
        for sch, ch, msg in r.iter_messages():
            fqn = sch.name.replace("::", ".")
            idl.setdefault(fqn, (sch.name, sch.data))
            items.append((msg.log_time,
                          {"kind": "sample", "topic": ch.topic,
                           "fqn": fqn, "raw": msg.data}))
        for md in r.iter_metadata():
            m = md.metadata
            attr = {"principal": m.get("principal", ""),
                    "scene": m.get("scene", "")}
            if md.name == WRITTEN_BY_RECORD:
                who[int(m["seq"])] = (m.get("principal", ""),
                                      m.get("scene", ""),
                                      float(m["refresh"]) if m.get("refresh")
                                      else None)
            elif md.name == DISPOSE_RECORD:
                items.append((int(m["seq"]),
                              {"kind": "dispose", "topic": m["topic"],
                               "key": m["key"], "reason": m.get("reason", ""),
                               **attr}))
            elif md.name == MANIFEST_RECORD:
                items.append((int(m["seq"]),
                              {"kind": "manifest", "uri": m["uri"],
                               "doc": m["doc"], **attr}))
            elif md.name == MANIFEST_WITHDRAW_RECORD:
                items.append((int(m["seq"]),
                              {"kind": "manifest-withdraw", "uri": m["uri"],
                               "reason": m.get("reason", ""), **attr}))
    for seq, e in items:
        if e["kind"] == "sample":
            p, sc, refresh = who.get(seq, ("", "", None))
            e["principal"], e["scene"] = p, sc or _topic_scene(e["topic"])
            e["refresh"] = refresh
        elif not e.get("scene"):
            e["scene"] = (gateway.uri_scene(e["uri"]) if "uri" in e
                          else _topic_scene(e["topic"]))
    items.sort(key=lambda x: x[0])
    return items, idl


def write_entries(path: Path, entries: list[dict],
                  idl: dict[str, tuple[str, bytes]]) -> None:
    """The whole journal, rewritten and renamed into place.

    Every entry carries its sequence number, so a filtered rewrite (a
    per-scene reset) keeps every surviving entry's seq, and therefore its
    order and its bytes, exactly.
    """
    fd, tmp = tempfile.mkstemp(dir=str(Path(path).parent),
                               suffix=".mcap.tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            w = McapWriter(f)
            w.start(profile="spatialdds", library="spatialdds-web/publish")
            sch_ids: dict[str, int] = {}
            ch_ids: dict[str, int] = {}
            for e in entries:
                if e["kind"] != "sample":
                    continue
                if e["fqn"] not in sch_ids:
                    name, data = idl[e["fqn"]]
                    sch_ids[e["fqn"]] = w.register_schema(
                        name=name, encoding="omgidl", data=data)
                if e["topic"] not in ch_ids:
                    ch_ids[e["topic"]] = w.register_channel(
                        topic=e["topic"], message_encoding="cdr",
                        schema_id=sch_ids[e["fqn"]])
                w.add_message(channel_id=ch_ids[e["topic"]],
                              log_time=e["seq"], publish_time=e["seq"],
                              data=e["raw"], sequence=e["seq"])
            for e in entries:
                attr = {"principal": e.get("principal", ""),
                        "scene": e.get("scene", "")}
                if e["kind"] == "sample":
                    # `refresh` only when there is one, so a journal of
                    # records is the same bytes it was before 0.8.0.
                    extra = {"refresh": f"{e['refresh']:g}"} \
                        if e.get("refresh") else {}
                    w.add_metadata(name=WRITTEN_BY_RECORD, data={
                        "seq": str(e["seq"]), **attr, **extra})
                elif e["kind"] == "dispose":
                    w.add_metadata(name=DISPOSE_RECORD, data={
                        "seq": str(e["seq"]), "topic": e["topic"],
                        "key": e["key"], "reason": e["reason"], **attr})
                elif e["kind"] == "manifest":
                    w.add_metadata(name=MANIFEST_RECORD, data={
                        "seq": str(e["seq"]), "uri": e["uri"],
                        "doc": e["doc"], **attr})
                elif e["kind"] == "manifest-withdraw":
                    w.add_metadata(name=MANIFEST_WITHDRAW_RECORD, data={
                        "seq": str(e["seq"]), "uri": e["uri"],
                        "reason": e["reason"], **attr})
            w.finish()
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def reset_scene(path: Path, scene: str) -> tuple[int, int]:
    """Remove one scene's entries from a journal, offline. (removed, kept)

    The operator action behind a per-lane reset: stop the service, run this,
    start it. The seed is not in the journal, so a seeded lane returns to its
    seed and an empty lane to empty. Every other scene's entries are written
    back with their own sequence numbers and bytes, so every other lane serves
    exactly what it served before.
    """
    items, idl = read_entries(path)
    kept = [{**e, "seq": seq} for seq, e in items if e.get("scene") != scene]
    write_entries(path, kept, idl)
    return len(items) - len(kept), len(kept)


def now_stamp() -> dict:
    t = time.time()
    return {"sec": str(int(t)), "nanosec": int((t % 1) * 1e9)}
