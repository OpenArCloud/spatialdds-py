"""A claim-shaped deployment extension for gates/entity_publishing.py.

The OWM 0.2 design's Relationship, cut to what the write surface reads: an
id, the proposition, a basis, an asserter, and the lifecycle members the
world model spells (`state`, `state_reason`, `stamp`). The basis enum is the
specification's own (`spatial::owm::Basis`), so a claim and an entity are
gated by the same values.

Its own module, without `from __future__ import annotations`: cyclonedds
resolves a field's annotation against the defining module's namespace, and a
string annotation does not resolve there (see `_ext_type.py`).
"""
from dataclasses import dataclass

from cyclonedds.idl import IdlEnum, IdlStruct, annotations as annotate

from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.owm import Basis

CLAIM_IDL = """#include "provisional/owm.idl"
module gate_claim {
  enum ClaimState { @value(0) CREATED, @value(1) REVISED, @value(2) RETIRED };
  @extensibility(APPENDABLE) struct Claim {
    @key string id;
    string subject_ref;
    string predicate;
    string object_ref;
    spatial::owm::Basis basis;
    string asserter;
    builtin::Time stamp;
    ClaimState state;
    string state_reason;
  };
};
"""


@annotate.final
class ClaimState(IdlEnum, typename="gate_claim.ClaimState", default="CREATED"):
    CREATED = 0
    REVISED = 1
    RETIRED = 2


@dataclass
@annotate.appendable
@annotate.autoid("sequential")
class Claim(IdlStruct, typename="gate_claim.Claim"):
    id: str
    annotate.key("id")
    subject_ref: str
    predicate: str
    object_ref: str
    basis: Basis
    asserter: str
    stamp: Time
    state: ClaimState
    state_reason: str
