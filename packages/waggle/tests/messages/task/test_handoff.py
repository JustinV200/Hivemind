"""Tests for waggle.messages.task.handoff and TaskAssign.handoff: a Handoff carried inline.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins HandoffDecision and HandoffDocument
    (construction, the JSON round trip, every field's bound at its limit and one past it, the
    id kind, the rejection of an extra field and of the taint label), and the two rules spec
    section 8.2 gives TaskAssign.handoff (set only with resume_from, and labelled exactly as
    resume_from is) in both directions; that a task.assign without it still validates and a
    maximal one still fits one frame. The family's EXAMPLES live in test_assignment.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.task.handoff and waggle.messages.task.assignment for the modules under test.
    - docs/waggle/spec.md section 8.2 for the fields, bounds and validators pinned here.
    - hivemind's tests/unit/memory/test_handoff_wire.py, which keeps HandoffDocument in step
      with hivemind.memory.Handoff.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.codec import MAX_FRAME_BYTES, Codec
from waggle.envelope import Envelope
from waggle.ids import IdKind, new_id
from waggle.messages.labels import (
    AccuracyBar,
    HandoffRef,
    HoneyClearance,
    Postcondition,
    PostconditionKind,
    Tempo,
)
from waggle.messages.task import TaskAssign, WorkerRole
from waggle.messages.task.handoff import (
    MAX_HANDOFF_DECISION_WHAT_CHARS,
    MAX_HANDOFF_DECISION_WHY_CHARS,
    MAX_HANDOFF_DECISIONS,
    MAX_HANDOFF_GOAL_CHARS,
    MAX_HANDOFF_LIST_ITEM_CHARS,
    MAX_HANDOFF_LIST_ITEMS,
    MAX_HANDOFF_NOTES_CHARS,
    MAX_HANDOFF_PROGRESS_CHARS,
    MAX_HANDOFF_WRITTEN_BY_CHARS,
    HandoffDecision,
    HandoffDocument,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
TASK_ID = new_id(IdKind.TASK, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
GRANT_ID = new_id(IdKind.GRANT, CLOCK)
EVENT_ID = new_id(IdKind.EVENT, CLOCK)
# The list-valued fields of a Handoff, each bounded by MAX_HANDOFF_LIST_ITEMS items of
# MAX_HANDOFF_LIST_ITEM_CHARS characters.
LIST_FIELDS = (
    "tried_and_failed",
    "constraints",
    "open_threads",
    "next_steps",
    "do_not_redo",
    "pinned_facts",
)


def _ref(clearance: HoneyClearance = HoneyClearance.C1) -> HandoffRef:
    """A reference to the Handoff's trail event, labelled ``clearance``."""
    return HandoffRef(event_id=EVENT_ID, written_at=NOW, clearance=clearance)


def _document(**overrides: object) -> HandoffDocument:
    """A small but complete Handoff labelled C1, then ``overrides``."""
    fields: dict[str, object] = {
        "goal": "Summarise the release notes into one page.",
        "progress": "Read four of the six files; drafted the first half.",
        "decisions": ({"what": "Use bullet points", "why": "The reader skims."},),
        "tried_and_failed": ("Concatenating the files verbatim: far too long.",),
        "constraints": ("The page must fit one A4 sheet.",),
        "open_threads": ("Whether to include the security notes.",),
        "next_steps": ("Read the remaining two files.",),
        "do_not_redo": ("Re-reading the first file.",),
        "pinned_facts": ("The release notes live under docs/release/.",),
        "notes": "The operator prefers short sentences.",
        "clearance": HoneyClearance.C1,
        "written_by": "worker_forager",
        "task_id": TASK_ID,
    }
    return HandoffDocument.model_validate({**fields, **overrides})


def _assign(**overrides: object) -> TaskAssign:
    """A task.assign resuming from a C1 reference, with no inline Handoff, then ``overrides``."""
    fields: dict[str, object] = {
        "task_id": TASK_ID,
        "goal_id": TASK_ID,
        "cell_id": CELL_ID,
        "role": WorkerRole.FORAGER,
        "slot": "FORAGER",
        "objective": "Summarise the release notes into one page.",
        "acceptance": (
            Postcondition(
                kind=PostconditionKind.JUDGE_RUBRIC,
                subject="review",
                argv=(),
                expected="The summary is faithful to the notes.",
            ),
        ),
        "tempo": Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        "clearance": HoneyClearance.C1,
        "grant_id": GRANT_ID,
        "attempt": 2,
        "resume_from": _ref(),
        "reason": "The Warden is migrating onto the device and cannot read the Hive Stand's store.",
    }
    return TaskAssign.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# HandoffDecision and HandoffDocument
# ──────────────────────────────────────────────────────────────────────────────


def test_handoff_document_round_trips_and_keeps_the_task_id_optional() -> None:
    document = _document()
    unattached = _document(task_id=None)
    empty_lists = _document(
        decisions=(),
        **dict.fromkeys(LIST_FIELDS, ()),
        notes="",
    )

    for item in (document, unattached, empty_lists):
        assert HandoffDocument.model_validate(item.model_dump(mode="json")) == item
    # Every field but task_id is mandatory: the mirror of hivemind's Handoff.
    without_task_id = {
        key: value for key, value in document.model_dump().items() if key != "task_id"
    }
    assert HandoffDocument.model_validate(without_task_id).task_id is None
    assert "tainted" not in HandoffDocument.model_fields


@pytest.mark.parametrize(
    "missing", [name for name in HandoffDocument.model_fields if name != "task_id"]
)
def test_handoff_document_requires_every_field_but_the_task_id(missing: str) -> None:
    fields = {key: value for key, value in _document().model_dump().items() if key != missing}

    with pytest.raises(ValidationError, match=missing):
        HandoffDocument.model_validate(fields)


def test_handoff_document_never_carries_a_taint_label() -> None:
    # A tainted Handoff is one nobody may resume from, so it never travels (ADR-0052).
    with pytest.raises(ValidationError, match="extra"):
        HandoffDocument.model_validate({**_document().model_dump(), "tainted": None})


def test_handoff_document_accepts_every_field_at_its_bound() -> None:
    document = _document(
        goal="g" * MAX_HANDOFF_GOAL_CHARS,
        progress="p" * MAX_HANDOFF_PROGRESS_CHARS,
        decisions=tuple(
            {
                "what": "w" * MAX_HANDOFF_DECISION_WHAT_CHARS,
                "why": "y" * MAX_HANDOFF_DECISION_WHY_CHARS,
            }
            for _ in range(MAX_HANDOFF_DECISIONS)
        ),
        notes="n" * MAX_HANDOFF_NOTES_CHARS,
        written_by="b" * MAX_HANDOFF_WRITTEN_BY_CHARS,
        **dict.fromkeys(LIST_FIELDS, ("i" * MAX_HANDOFF_LIST_ITEM_CHARS,) * MAX_HANDOFF_LIST_ITEMS),
    )

    assert len(document.decisions) == MAX_HANDOFF_DECISIONS
    assert all(len(getattr(document, name)) == MAX_HANDOFF_LIST_ITEMS for name in LIST_FIELDS)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"goal": ""}, "at least 1"),
        ({"goal": "g" * (MAX_HANDOFF_GOAL_CHARS + 1)}, f"at most {MAX_HANDOFF_GOAL_CHARS}"),
        ({"progress": ""}, "at least 1"),
        (
            {"progress": "p" * (MAX_HANDOFF_PROGRESS_CHARS + 1)},
            f"at most {MAX_HANDOFF_PROGRESS_CHARS}",
        ),
        (
            {"decisions": ({"what": "w", "why": "y"},) * (MAX_HANDOFF_DECISIONS + 1)},
            f"at most {MAX_HANDOFF_DECISIONS}",
        ),
        ({"decisions": ({"what": "", "why": "y"},)}, "at least 1"),
        ({"decisions": ({"what": "w", "why": ""},)}, "at least 1"),
        (
            {"decisions": ({"what": "w" * (MAX_HANDOFF_DECISION_WHAT_CHARS + 1), "why": "y"},)},
            f"at most {MAX_HANDOFF_DECISION_WHAT_CHARS}",
        ),
        (
            {"decisions": ({"what": "w", "why": "y" * (MAX_HANDOFF_DECISION_WHY_CHARS + 1)},)},
            f"at most {MAX_HANDOFF_DECISION_WHY_CHARS}",
        ),
        ({"decisions": ({"what": "w", "why": "y", "by": "me"},)}, "extra"),
        ({"notes": "n" * (MAX_HANDOFF_NOTES_CHARS + 1)}, f"at most {MAX_HANDOFF_NOTES_CHARS}"),
        (
            {"written_by": "b" * (MAX_HANDOFF_WRITTEN_BY_CHARS + 1)},
            f"at most {MAX_HANDOFF_WRITTEN_BY_CHARS}",
        ),
        ({"clearance": "C3"}, "clearance"),
        ({"task_id": CELL_ID}, "task_"),
        ({"owner": "queen"}, "extra"),
    ],
)
def test_handoff_document_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _document(**changes)


@pytest.mark.parametrize("name", LIST_FIELDS)
def test_handoff_document_list_fields_bound_their_length_and_their_items(name: str) -> None:
    with pytest.raises(ValidationError, match=f"at most {MAX_HANDOFF_LIST_ITEMS}"):
        _document(**{name: ("x",) * (MAX_HANDOFF_LIST_ITEMS + 1)})
    with pytest.raises(ValidationError, match=f"at most {MAX_HANDOFF_LIST_ITEM_CHARS}"):
        _document(**{name: ("x" * (MAX_HANDOFF_LIST_ITEM_CHARS + 1),)})


def test_handoff_decision_round_trips_and_is_frozen() -> None:
    decision = HandoffDecision(what="Use bullet points", why="The reader skims.")

    assert HandoffDecision.model_validate(decision.model_dump(mode="json")) == decision
    with pytest.raises(ValidationError, match="frozen"):
        decision.what = "changed"  # The assignment is the test: the model must refuse it.


# ──────────────────────────────────────────────────────────────────────────────
# TaskAssign.handoff
# ──────────────────────────────────────────────────────────────────────────────


def test_task_assign_handoff_defaults_to_none_so_an_older_peers_assign_still_validates() -> None:
    assign = _assign()
    older = {key: value for key, value in assign.model_dump().items() if key != "handoff"}

    assert assign.handoff is None
    assert TaskAssign.model_validate(older) == assign


def test_task_assign_carries_a_handoff_labelled_as_its_reference_is() -> None:
    document = _document()
    assign = _assign(handoff=document)

    assert assign.handoff == document
    assert TaskAssign.model_validate(assign.model_dump(mode="json")) == assign
    # Every label works, so long as the reference and the document agree and the task may read it.
    for clearance in HoneyClearance:
        carried = _assign(
            clearance=HoneyClearance.C2,
            resume_from=_ref(clearance),
            handoff=_document(clearance=clearance),
        )
        assert carried.handoff is not None
        assert carried.handoff.clearance is clearance


def test_task_assign_refuses_a_handoff_with_nothing_to_store_it_under() -> None:
    with pytest.raises(ValidationError, match="only together with resume_from"):
        _assign(resume_from=None, handoff=_document())


@pytest.mark.parametrize(
    ("reference", "document"),
    [
        (HoneyClearance.C1, HoneyClearance.C0),  # A document labelled lower than advertised.
        (HoneyClearance.C1, HoneyClearance.C2),  # And one labelled higher.
        (HoneyClearance.C0, HoneyClearance.C1),
    ],
)
def test_task_assign_refuses_a_handoff_labelled_differently_from_its_reference(
    reference: HoneyClearance, document: HoneyClearance
) -> None:
    with pytest.raises(ValidationError, match="they must match"):
        _assign(
            clearance=HoneyClearance.C2,
            resume_from=_ref(reference),
            handoff=_document(clearance=document),
        )


def test_task_assign_still_refuses_a_reference_above_the_task_s_clearance() -> None:
    # The existing rule is untouched by the new field: it applies to the reference, and an inline
    # document labelled the same is therefore above the task's clearance too.
    with pytest.raises(ValidationError, match="above the task's clearance"):
        _assign(
            clearance=HoneyClearance.C0,
            resume_from=_ref(HoneyClearance.C1),
            handoff=_document(clearance=HoneyClearance.C1),
        )


def test_task_assign_handoff_travels_through_the_codec(
    make_envelope: Callable[..., Envelope], plain_codec: Codec
) -> None:
    assign = _assign(handoff=_document())

    decoded = plain_codec.decode(plain_codec.encode(make_envelope(assign)))

    assert decoded.payload == assign


def test_a_maximal_handoff_still_fits_one_frame(
    make_envelope: Callable[..., Envelope], plain_codec: Codec
) -> None:
    maximal = _document(
        goal="g" * MAX_HANDOFF_GOAL_CHARS,
        progress="p" * MAX_HANDOFF_PROGRESS_CHARS,
        decisions=tuple(
            {
                "what": "w" * MAX_HANDOFF_DECISION_WHAT_CHARS,
                "why": "y" * MAX_HANDOFF_DECISION_WHY_CHARS,
            }
            for _ in range(MAX_HANDOFF_DECISIONS)
        ),
        notes="n" * MAX_HANDOFF_NOTES_CHARS,
        written_by="b" * MAX_HANDOFF_WRITTEN_BY_CHARS,
        **dict.fromkeys(LIST_FIELDS, ("i" * MAX_HANDOFF_LIST_ITEM_CHARS,) * MAX_HANDOFF_LIST_ITEMS),
    )

    frame = plain_codec.encode(make_envelope(_assign(handoff=maximal)))

    # Ordinary text; a document of wide characters can still exceed the ceiling, in which case the
    # Queen's encode raises waggle.codec.too_large and the task starts fresh (spec section 8.2).
    assert len(frame) < MAX_FRAME_BYTES // 2
