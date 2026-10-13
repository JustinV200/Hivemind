"""Tests that waggle's HandoffDocument mirrors hivemind.memory.Handoff, field for field.

A Handoff normally travels as a reference, because the receiving Warden reads the document from the
memory store it shares with the sender. When it cannot (a Warden migrating onto a colonized device,
a task reassigned across nodes) the Queen carries the document itself in `TaskAssign.handoff`
(Waggle 1.13, ADR-0052), as `waggle.messages.task.HandoffDocument`. That wire model lives in waggle
and so cannot import the hivemind one, which makes this test the only thing that notices when the
two drift: it compares their JSON schemas with the prose stripped and `tainted` removed, pins the
numeric bounds one by one, and checks both directions of the conversion the Queen and the Warden
make.

Fits into the Hive:
    Mirrors src/hivemind/memory/handoff.py (codingrules section 3: tests/unit mirrors src/
    one-to-one), split by feature from test_handoff.py (codingrules 5.1). Reads waggle's
    `waggle.messages.task.handoff` as the other side of the mirror.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.handoff for Handoff and Decision, the models mirrored.
    - waggle.messages.task.handoff for HandoffDocument and HandoffDecision.
    - docs/waggle/spec.md section 8.2 for the wire form.
"""

from __future__ import annotations

from typing import Any

import pytest
from builders.memory import make_handoff

from hivemind.memory import handoff as hivemind_handoff
from hivemind.memory.handoff import Decision, Handoff
from waggle.messages.task import handoff as wire_handoff
from waggle.messages.task.handoff import HandoffDecision, HandoffDocument

# Each hivemind bound and the waggle constant that must equal it. The names differ on purpose (a
# wire constant carries `HANDOFF_` so it never reads as an unrelated bound), the numbers may not.
_BOUND_PAIRS = (
    ("MIN_GOAL_CHARS", "MIN_HANDOFF_GOAL_CHARS"),
    ("MAX_GOAL_CHARS", "MAX_HANDOFF_GOAL_CHARS"),
    ("MIN_PROGRESS_CHARS", "MIN_HANDOFF_PROGRESS_CHARS"),
    ("MAX_PROGRESS_CHARS", "MAX_HANDOFF_PROGRESS_CHARS"),
    ("MAX_DECISION_WHAT_CHARS", "MAX_HANDOFF_DECISION_WHAT_CHARS"),
    ("MAX_DECISION_WHY_CHARS", "MAX_HANDOFF_DECISION_WHY_CHARS"),
    ("MAX_DECISIONS", "MAX_HANDOFF_DECISIONS"),
    ("MAX_LIST_ITEM_CHARS", "MAX_HANDOFF_LIST_ITEM_CHARS"),
    ("MAX_LIST_ITEMS", "MAX_HANDOFF_LIST_ITEMS"),
    ("MAX_NOTES_CHARS", "MAX_HANDOFF_NOTES_CHARS"),
    ("MAX_WRITTEN_BY_CHARS", "MAX_HANDOFF_WRITTEN_BY_CHARS"),
)


def _shape(schema: dict[str, Any], node: object) -> object:
    """Resolve ``$ref``s and drop the prose, so two schemas compare by shape alone."""
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return _shape(schema, schema["$defs"][name])
        # `title` and `description` are the models' own words (and names); `$defs` is the pool the
        # refs above were resolved from. Everything else is a constraint worth comparing.
        skipped = {"title", "description", "$defs"}
        return {key: _shape(schema, value) for key, value in node.items() if key not in skipped}
    if isinstance(node, list):
        return [_shape(schema, item) for item in node]
    return node


def test_the_wire_document_has_the_shape_of_the_hivemind_handoff_minus_the_taint_label() -> None:
    mirrored = Handoff.model_json_schema()
    wire = HandoffDocument.model_json_schema()
    # The one field the wire form deliberately lacks: a tainted Handoff never travels.
    del mirrored["properties"]["tainted"]

    assert _shape(wire, wire) == _shape(mirrored, mirrored)


def test_the_wire_decision_has_the_shape_of_the_hivemind_decision() -> None:
    mirrored = Decision.model_json_schema()
    wire = HandoffDecision.model_json_schema()

    assert _shape(wire, wire) == _shape(mirrored, mirrored)


def test_the_wire_document_lists_the_fields_of_the_handoff_but_the_taint_label() -> None:
    assert list(HandoffDocument.model_fields) == [
        name for name in Handoff.model_fields if name != "tainted"
    ]


@pytest.mark.parametrize(("hivemind_name", "wire_name"), _BOUND_PAIRS)
def test_each_wire_bound_equals_the_hivemind_one(hivemind_name: str, wire_name: str) -> None:
    assert getattr(wire_handoff, wire_name) == getattr(hivemind_handoff, hivemind_name)


def test_every_hivemind_bound_has_a_pair_so_a_new_one_cannot_be_forgotten() -> None:
    bounds = {name for name in hivemind_handoff.__all__ if name.startswith(("MIN_", "MAX_"))}

    assert bounds == {hivemind_name for hivemind_name, _ in _BOUND_PAIRS}


def test_a_handoff_becomes_a_document_and_back_without_losing_a_field() -> None:
    handoff = make_handoff()

    document = HandoffDocument.model_validate(handoff.model_dump(mode="json", exclude={"tainted"}))
    restored = Handoff.model_validate(document.model_dump(mode="json"))

    assert restored == handoff
    assert document.clearance.value == handoff.clearance.value
    assert document.task_id == handoff.task_id


def test_a_handoff_still_carrying_its_taint_label_is_not_a_valid_document() -> None:
    # The Queen drops the label by excluding the field; a caller that forgot would send a frame
    # the wire model rejects outright, rather than a tainted Handoff that travels.
    handoff = make_handoff()

    with pytest.raises(ValueError, match="tainted"):
        HandoffDocument.model_validate(handoff.model_dump(mode="json"))
