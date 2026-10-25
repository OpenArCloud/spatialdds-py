"""A deployment extension type for gates/native_additions.py, as idlc would write it.

Its own module, without `from __future__ import annotations`: cyclonedds
resolves a field's annotation against the defining module's namespace, and a
string annotation, `str` included, does not resolve there.

The IDL it is generated from is `EXT_IDL` below. It includes a pinned file for
`FrameRef`, which is what `sd.dds.extend()` has to close over.
"""
from dataclasses import dataclass

from cyclonedds.idl import IdlStruct, annotations as annotate, types

from spatialdds.dds.types.spatial.common import FrameRef

EXT_IDL = """#include "common.idl"
module gate_ext {
  @extensibility(APPENDABLE) struct Reading {
    @key string reading_id;
    string label;
    double value;
    spatial::common::FrameRef frame_ref;
  };
  @extensibility(APPENDABLE) struct Note {
    @key string note_id;
    string text;
  };
};
"""


@dataclass
@annotate.appendable
@annotate.autoid("sequential")
class Reading(IdlStruct, typename="gate_ext.Reading"):
    reading_id: str
    annotate.key("reading_id")
    label: str
    value: types.float64
    frame_ref: FrameRef


@dataclass
@annotate.appendable
@annotate.autoid("sequential")
class Note(IdlStruct, typename="gate_ext.Note"):
    note_id: str
    annotate.key("note_id")
    text: str
