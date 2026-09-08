"""Tests for the forage family's messages (waggle.messages.forage and forage_hosting): all eight.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    forage message class, which the registry step later loads by file path to seed its
    per-family checks; and for every class pins construction, the JSON round trip, the
    rejection of an extra field, at least one bound, and every validator spec section 8.4
    names. The family's value models and enums are tested in test_forage_values.py.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the eight forage message classes.

See Also:
    - waggle.messages.forage and waggle.messages.forage_hosting for the modules under test.
    - docs/waggle/spec.md section 8.4 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, MAX_SUB_BEES_ON_WIRE, WaggleMessage
from waggle.messages.forage import (
    MAX_ALLOWED_BINDINGS,
    MAX_SEAT_RESERVATIONS,
    ForageReply,
    ForageRequest,
    GrantIssued,
    GrantRevoked,
)
from waggle.messages.forage_capacity import (
    CapacityTrigger,
    CeilingsReport,
    HostingMode,
    LocalPoolUsage,
    LocalSourceReport,
    ModelServerReport,
    SlotPlan,
    SourceChain,
)
from waggle.messages.forage_hosting import (
    MAX_MODEL_SERVERS,
    MAX_SLOT_PLANS,
    CapacityReport,
    CeilingsSet,
    HostingDecided,
    PlanWritten,
)
from waggle.messages.forage_values import (
    AllowedBinding,
    Effort,
    ForageDelta,
    ForageOutcome,
    ForageRequestKind,
    RevocationCause,
    SeatReservation,
    SourceRef,
)
from waggle.messages.labels import AccuracyBar, Tempo
from waggle.messages.reports import HostCapacityReport

CLOCK = FakeClock()
NOW = CLOCK.now()
LATER = NOW + timedelta(hours=1)
GIB = 1_073_741_824  # One gibibyte, so the figures below read as a real host.
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
GRANT_ID = new_id(IdKind.GRANT, CLOCK)
TASK_ID = new_id(IdKind.TASK, CLOCK)
FORAGE_CLASSES: tuple[type[WaggleMessage], ...] = (
    CapacityReport,
    GrantIssued,
    GrantRevoked,
    ForageRequest,
    ForageReply,
    HostingDecided,
    CeilingsSet,
    PlanWritten,
)

# The value models the examples are built from: one hosted source, one local source on CELL_ID.
HOSTED = SourceRef(
    source_id="hosted/strong", provider="hosted", model="strong-v1", host_cell_id=None
)
LOCAL = SourceRef(
    source_id="nuc/small", provider="local_server", model="small-8b", host_cell_id=CELL_ID
)
HOST = HostCapacityReport(
    cores=8,
    memory_bytes=32 * GIB,
    memory_free_bytes=16 * GIB,
    disk_bytes=512 * GIB,
    disk_free_bytes=100 * GIB,
    cpu_load=0.25,
    gpus=(),
)
SERVER = ModelServerReport(
    server_id="nuc_server",
    sources=(
        LocalSourceReport(
            model="small-8b",
            seats_total=4,
            seats_free=3,
            context_window=8_192,
            vram_bytes=5 * GIB,
            tokens_per_s=42.0,
        ),
    ),
)
USAGE = LocalPoolUsage(
    sub_bees_active=2, model_vram_bytes=4 * GIB, model_disk_bytes=8 * GIB, seats_exported=1
)
CEILINGS = CeilingsReport(
    max_sub_bees=4,
    model_vram_bytes=8 * GIB,
    model_disk_bytes=32 * GIB,
    loadable_sources=("nuc/small",),
    exportable_seats=2,
)
ALLOWED = AllowedBinding(slot="PLANNER", source=HOSTED, max_effort=Effort.HIGH)
RESERVATION = SeatReservation(
    source_id="hosted/strong", seats=2, requests_per_minute=60, tokens_per_minute=None
)
TEMPO = Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL)
SEATS_WANTED = ForageDelta(seats=2, source_id="hosted/strong", slot=None, minimum_grade=None)
NOTHING = ForageDelta(source_id=None, slot=None, minimum_grade=None)

# One valid instance of every forage class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    CapacityReport(
        cell_id=CELL_ID,
        trigger=CapacityTrigger.PERIODIC,
        host=HOST,
        model_servers=(SERVER,),
        max_sub_bees=8,
        usage=USAGE,
    ),
    GrantIssued(
        grant_id=GRANT_ID,
        holder=WARDEN_ID,
        cell_id=CELL_ID,
        task_id=TASK_ID,
        revision=0,
        allowed=(ALLOWED,),
        seats=(RESERVATION,),
        token_budget=1_000_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=4,
        expires_at=LATER,
        reason="Sized to the goal's caps.",
    ),
    GrantRevoked(
        grant_id=GRANT_ID,
        holder=WARDEN_ID,
        revision=0,
        cause=RevocationCause.RECLAIMED,
        reason="A CRITICAL task needs the seats.",
    ),
    ForageRequest(
        grant_id=GRANT_ID,
        kind=ForageRequestKind.SHARED_SEATS,
        wanted=SEATS_WANTED,
        task_id=TASK_ID,
        tempo=TEMPO,
        reason="Two Workers wait on one seat.",
    ),
    ForageReply(
        grant_id=GRANT_ID,
        outcome=ForageOutcome.PARTIAL,
        granted=ForageDelta(seats=1, source_id="hosted/strong", slot=None, minimum_grade=None),
        revision=1,
        expires_at=LATER,
        reason="One seat of headroom remains.",
    ),
    HostingDecided(
        cell_id=CELL_ID,
        revision=0,
        mode=HostingMode.LOCAL_FIRST,
        task_id=None,
        reason="Free VRAM covers the small slots.",
    ),
    CeilingsSet(
        cell_id=CELL_ID,
        holder=WARDEN_ID,
        revision=0,
        ceilings=CEILINGS,
        reason="A Nuc with one GPU.",
    ),
    PlanWritten(
        cell_id=CELL_ID,
        revision=0,
        slots=(SlotPlan(slot="PLANNER", chain=SourceChain(primary=HOSTED, fallbacks=())),),
        default=SourceChain(primary=LOCAL, fallbacks=(HOSTED,)),
        reason="Local first, hosted fallback.",
    ),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


def _slot_plan(slot: str) -> SlotPlan:
    """A plan entry for ``slot`` whose chain is the hosted source alone."""
    return SlotPlan(slot=slot, chain=SourceChain(primary=HOSTED, fallbacks=()))


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_forage_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == FORAGE_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_forage_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.cell_id = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_forage_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize(
    "example", [example for example in EXAMPLES if not isinstance(example, CapacityReport)]
)
def test_reason_is_bounded_by_the_shared_limit(example: WaggleMessage) -> None:
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


@pytest.mark.parametrize(
    "message_type", [GrantIssued, GrantRevoked, HostingDecided, CeilingsSet, PlanWritten]
)
def test_revision_is_never_negative(message_type: type[WaggleMessage]) -> None:
    assert _rebuild(_example(message_type), revision=7)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _rebuild(_example(message_type), revision=-1)


@pytest.mark.parametrize(
    ("message_type", "field", "prefix"),
    [
        (CapacityReport, "cell_id", "cell_"),
        (GrantIssued, "holder", "warden_"),
        (GrantRevoked, "grant_id", "grant_"),
        (ForageRequest, "task_id", "task_"),
        (CeilingsSet, "holder", "warden_"),
    ],
)
def test_id_fields_reject_an_id_of_another_kind(
    message_type: type[WaggleMessage], field: str, prefix: str
) -> None:
    with pytest.raises(ValidationError, match=prefix):
        _rebuild(_example(message_type), **{field: new_id(IdKind.NODE, CLOCK)})


# ──────────────────────────────────────────────────────────────────────────────
# CapacityReport, HostingDecided, CeilingsSet, PlanWritten
# ──────────────────────────────────────────────────────────────────────────────


def test_capacity_report_bounds_its_servers_and_sub_bee_cap() -> None:
    example = _example(CapacityReport)
    assert _rebuild(example, model_servers=(), max_sub_bees=MAX_SUB_BEES_ON_WIRE)
    with pytest.raises(ValidationError, match=f"at most {MAX_MODEL_SERVERS}"):
        _rebuild(example, model_servers=(SERVER,) * (MAX_MODEL_SERVERS + 1))
    with pytest.raises(ValidationError, match=f"less than or equal to {MAX_SUB_BEES_ON_WIRE}"):
        _rebuild(example, max_sub_bees=MAX_SUB_BEES_ON_WIRE + 1)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _rebuild(example, max_sub_bees=-1)


# ──────────────────────────────────────────────────────────────────────────────
# GrantIssued, GrantRevoked, ForageRequest, ForageReply
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"allowed": (ALLOWED,) * (MAX_ALLOWED_BINDINGS + 1)}, f"at most {MAX_ALLOWED_BINDINGS}"),
        (
            {"seats": (RESERVATION,) * (MAX_SEAT_RESERVATIONS + 1)},
            f"at most {MAX_SEAT_RESERVATIONS}",
        ),
        ({"token_budget": -1}, "greater than or equal to 0"),
        ({"spend_budget": -0.5}, "greater than or equal to 0"),
        ({"tokens_spent": -1}, "greater than or equal to 0"),
        ({"spent": -0.5}, "greater than or equal to 0"),
        ({"max_sub_bees": MAX_SUB_BEES_ON_WIRE + 1}, "less than or equal to"),
        ({"expires_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
    ],
)
def test_grant_issued_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(GrantIssued), **changes)


def test_grant_issued_standing_grant_has_no_task() -> None:
    standing = _rebuild(_example(GrantIssued), task_id=None, allowed=(), seats=())

    assert isinstance(standing, GrantIssued)
    assert standing.task_id is None
    assert standing.model_dump(mode="json")["allowed"] == []


def test_grant_revoked_carries_every_cause() -> None:
    for cause in RevocationCause:
        revoked = _rebuild(_example(GrantRevoked), cause=cause.value)
        assert isinstance(revoked, GrantRevoked)
        assert revoked.cause is cause


def test_forage_request_rejects_an_empty_ask_and_accepts_a_binding_ask() -> None:
    example = _example(ForageRequest)
    binding = _rebuild(
        example,
        kind="BINDING",
        wanted=ForageDelta(source_id=None, slot="JUDGE", minimum_grade=4),
        task_id=None,
    )

    assert isinstance(binding, ForageRequest)
    assert binding.wanted.slot == "JUDGE"
    with pytest.raises(ValidationError, match="at least one non-zero field"):
        _rebuild(example, wanted=NOTHING)


def test_forage_reply_denied_carries_no_terms() -> None:
    denied = _rebuild(
        _example(ForageReply), outcome="DENIED", granted=NOTHING, revision=None, expires_at=None
    )

    assert isinstance(denied, ForageReply)
    assert denied.granted.is_empty
    assert denied.revision is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"outcome": "DENIED", "granted": NOTHING, "expires_at": None}, "revision must be None"),
        ({"outcome": "DENIED", "granted": NOTHING, "revision": None}, "expires_at must be None"),
        ({"outcome": "DENIED", "revision": None, "expires_at": None}, "all-zero granted"),
        ({"revision": None}, "revision must be None exactly when denied"),
        ({"expires_at": None}, "expires_at must be None exactly when denied"),
        ({"outcome": "GRANTED", "revision": -1}, "greater than or equal to 0"),
    ],
)
def test_forage_reply_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(ForageReply), **changes)


def test_hosting_decided_carries_every_mode_and_an_optional_task() -> None:
    for mode in HostingMode:
        decided = _rebuild(_example(HostingDecided), mode=mode.value, task_id=TASK_ID)
        assert isinstance(decided, HostingDecided)
        assert decided.mode is mode
        assert decided.task_id == TASK_ID


def test_plan_written_slot_names_are_unique_and_bounded() -> None:
    example = _example(PlanWritten)
    two = _rebuild(example, slots=(_slot_plan("PLANNER"), _slot_plan("JUDGE")))

    assert isinstance(two, PlanWritten)
    assert [plan.slot for plan in two.slots] == ["PLANNER", "JUDGE"]
    assert _rebuild(example, slots=()).model_dump(mode="json")["slots"] == []
    with pytest.raises(ValidationError, match="slot names must be unique"):
        _rebuild(example, slots=(_slot_plan("PLANNER"), _slot_plan("PLANNER")))
    with pytest.raises(ValidationError, match=f"at most {MAX_SLOT_PLANS}"):
        # Slot names take letters only: the seventeen names are S, SA, SAA, ... not numbered.
        _rebuild(example, slots=tuple(_slot_plan("S" + "A" * i) for i in range(MAX_SLOT_PLANS + 1)))
