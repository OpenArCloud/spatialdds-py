"""The OWM 0.2 relationship prototype, as the sandbox serves it: `oarc.relationship`.

SpatialDDS-demo's demo-owned type (idl/demo18/oarc_owm02.idl, registered
there by spatialdds_demo/lanes.py), standing alone so the sandbox can serve
claims without the demo repository on the box:

    PYTHONPATH=/opt/spatialdds/extensions \
        python -m spatialdds.serve ... --extension sandbox_owm02

The IDL below is the demo's file verbatim; the classes are its generated ones
(spatialdds_demo_idl18/oarc_owm), with the spec's types taken from the
package. Its own module, without `from __future__ import annotations`:
cyclonedds resolves a member's annotation in the defining module's namespace.
Needs spatialdds[dds] (cyclonedds) for sd.dds.extend().
"""
from dataclasses import dataclass

import spatialdds as sd
from cyclonedds.idl import IdlEnum, IdlStruct, annotations as annotate, types

from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.owm import KV, Basis

NAME = "oarc.relationship"
OWM02_IDL = r"""// A prototype of the spatial.owm 0.2 Relationship, as a demo-owned type.
//
// spatial.owm/0.1 reserves relationships as open design (SpatialDDS-spec,
// Appendix E, "Reserved"). This is the 0.2 design's shape, prototyped the way
// oarc_model prototyped 0.1 before that module existed: a deployment
// extension (§3.3.2), registered with the SDK by spatialdds_demo/lanes.py,
// carrying nothing the spec is asked to adopt. Its output is evidence for the
// design (docs/owm-02-worked-example.md), not wire text for 1.9.
//
// Census and ruling: docs/owm-02-census.md.
//
// Optional members follow the pinned IDL's convention, a `has_*` flag before
// the value (owm.idl's has_pose, has_extent), because the pin uses no
// @optional. An empty `derived_from` means "not derived".

#ifndef OARC_OWM02_INCLUDED
#define OARC_OWM02_INCLUDED

#ifndef SPATIAL_OWM_INCLUDED
#include "provisional/owm.idl"
#endif

module oarc_owm {

  const string MODULE_ID = "oarc.owm/0.2-prototype";

  typedef builtin::Time Time;

  // The edge's own lifecycle. CREATED on the first sample of an id, REVISED
  // when the same asserter changes its opinion about the same proposition
  // (a new confidence, a new valid_from), RETIRED on the terminal sample
  // written before the instance is disposed, with the reason beside it.
  enum RelationshipLifecycle { @value(0) CREATED, @value(1) REVISED,
                               @value(2) RETIRED };

  // One evidence record a derived claim read: a claim or an entity sample,
  // named by the lane it came from, its instance key, and its stamp. The
  // stamp is what makes it a record rather than a name: a fast-lane pose and
  // the latched entity of the same robot share a key and differ here.
  @extensibility(APPENDABLE) struct EvidenceRef {
    string topic;
    string key;
    Time   stamp;
  };

  @extensibility(APPENDABLE) struct Relationship {
    // Publisher-namespaced: one asserter, one (subject, predicate, object),
    // one id, so a revision lands on the same instance and a second asserter
    // is always a second edge.
    @key string id;
    string subject_ref;                       // an Entity.entity_id
    string predicate;                         // a type URI
    string object_ref;                        // an Entity.entity_id
    spatial::owm::Basis basis;                // the same enum Entity carries
    string asserter;                          // the asserting service_id
    boolean has_confidence;
    double  confidence;                       // [0,1], within one asserter
    sequence<EvidenceRef, 16> derived_from;
    Time    stamp;                            // assertion time
    boolean has_valid_from;
    Time    valid_from;                       // world time the claim is about
    boolean has_valid_until;
    Time    valid_until;                      // unset: "while asserted"
    sequence<spatial::owm::KV, 8> meta;       // namespaced qualifiers
    // Named as Entity names them, and not for symmetry alone: the SDK finds
    // a terminal sample's members by these names (Writer._terminal), so a
    // withdraw() carries RETIRED and its reason on the wire only if the
    // members are spelled this way.
    RelationshipLifecycle state;
    string  state_reason;
  };

};

#endif // OARC_OWM02_INCLUDED
"""


@annotate.final
class RelationshipLifecycle(IdlEnum, typename="oarc_owm.RelationshipLifecycle",
                            default="CREATED"):
    CREATED = 0
    REVISED = 1
    RETIRED = 2


@dataclass
@annotate.appendable
@annotate.autoid("sequential")
class EvidenceRef(IdlStruct, typename="oarc_owm.EvidenceRef"):
    topic: str
    key: str
    stamp: Time


@dataclass
@annotate.appendable
@annotate.autoid("sequential")
class Relationship(IdlStruct, typename="oarc_owm.Relationship"):
    id: str
    annotate.key("id")
    subject_ref: str
    predicate: str
    object_ref: str
    basis: Basis
    asserter: str
    has_confidence: bool
    confidence: types.float64
    derived_from: types.sequence[EvidenceRef, 16]
    stamp: Time
    has_valid_from: bool
    valid_from: Time
    has_valid_until: bool
    valid_until: Time
    meta: types.sequence[KV, 8]
    state: RelationshipLifecycle
    state_reason: str


EXTENSION = sd.dds.extend(NAME, Relationship, OWM02_IDL)
