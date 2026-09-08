"""Tests for the honey family (waggle.messages.honey and honey_hit): three classes, two values.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    honey class, which the registry step later loads by file path to seed its per-family
    checks; and for every class pins construction, the JSON round trip (bytes included), the
    rejection of an extra field, at least one bound, and every validator spec section 8.7 names.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the three honey classes.

See Also:
    - waggle.messages.honey and waggle.messages.honey_hit for the modules under test.
    - docs/waggle/spec.md section 8.7 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest
from pydantic import BaseModel, ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_CHUNK_BYTES, MAX_REASON_CHARS, WaggleMessage
from waggle.messages.honey import (
    DEFAULT_MAX_HITS,
    MAX_MAX_HITS,
    MAX_MEDIA_TYPE_CHARS,
    MAX_QUERY_CHARS,
    MAX_SCOPES,
    MAX_TITLE_CHARS,
    HoneyQuery,
    HoneyResponse,
    NectarDeposit,
    NectarKind,
)
from waggle.messages.honey_hit import (
    MAX_EXCERPT_CHARS,
    MAX_HIT_TITLE_CHARS,
    MAX_HONEY_REF_CHARS,
    MAX_SCOPE_CHARS,
    HoneyHit,
    HoneyProvenance,
)
from waggle.messages.labels import CombShieldLevel, HoneyClearance

CLOCK = FakeClock()
NOW = CLOCK.now()
CONTENT = b"The Landing Board enrols a device on loopback only."
SHA256 = hashlib.sha256(CONTENT).hexdigest()
HONEY_CLASSES: tuple[type[WaggleMessage], ...] = (NectarDeposit, HoneyQuery, HoneyResponse)

PROVENANCE = HoneyProvenance(
    task_id=new_id(IdKind.TASK, CLOCK),
    cell_id=new_id(IdKind.CELL, CLOCK),
    bee=new_id(IdKind.WORKER, CLOCK),
    observed_at=NOW,
)
HIT = HoneyHit(
    honey_ref="honey/entrance/landing-board",
    title="How the Landing Board enrols a device",
    excerpt="Approval routes never exist on the remote listener.",
    score=0.87,
    scope="hive",
    clearance=HoneyClearance.C1,
    origin_tier=CombShieldLevel.MEADOW,
    provenance=PROVENANCE,
)

# One valid instance of every honey class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    NectarDeposit(
        sha256=SHA256,
        kind=NectarKind.FINDING,
        media_type="text/markdown",
        title="Landing Board enrolment notes",
        task_id=new_id(IdKind.TASK, CLOCK),
        cell_id=new_id(IdKind.CELL, CLOCK),
        worker_id=new_id(IdKind.WORKER, CLOCK),
        observed_at=NOW,
        clearance=HoneyClearance.C1,
        origin_tier=CombShieldLevel.MEADOW,
        event_id=None,
        chunk=CONTENT,
        offset=0,
        total_bytes=len(CONTENT),
        final=True,
    ),
    HoneyQuery(
        text="How does a device enrol?",
        requester=new_id(IdKind.WORKER, CLOCK),
        scopes=("hive", "task:task_01ARZ3NDEKTSV4RRFFQ69G5FAV"),
        max_tokens=2_000,
        max_clearance=HoneyClearance.C1,
        task_id=None,
    ),
    HoneyResponse(
        hits=(HIT,),
        token_count=120,
        is_truncated=False,
        filtered_count=2,
        reason="Two ranked rows above C1 were withheld by the caller's clearance.",
    ),
)


def _rebuild(example: BaseModel, **changes: object) -> BaseModel:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_honey_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == HONEY_CLASSES


@pytest.mark.parametrize("example", [*EXAMPLES, HIT, PROVENANCE], ids=lambda e: type(e).__name__)
def test_honey_model_round_trips_and_is_frozen(example: BaseModel) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.reason = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", [*EXAMPLES, HIT, PROVENANCE], ids=lambda e: type(e).__name__)
def test_honey_model_rejects_an_extra_field(example: BaseModel) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


def test_response_reason_is_bounded_by_the_shared_limit() -> None:
    example = _example(HoneyResponse)
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


def test_nectar_kind_members_and_values_match_the_spec() -> None:
    names = [
        "FINDING",
        "TRANSCRIPT",
        "TOOL_RESULT",
        "PATROL_SUMMARY",
        "AUDIT_FINDING",
        "FLIGHT_RECORDING",
        "HANDOFF",
        "RIPENED_HONEY",
    ]
    assert [member.name for member in NectarKind] == names
    assert all(member.value == member.name for member in NectarKind)


# ──────────────────────────────────────────────────────────────────────────────
# NectarDeposit
# ──────────────────────────────────────────────────────────────────────────────


def test_nectar_deposit_carries_its_chunk_as_base64_on_the_wire() -> None:
    example = _example(NectarDeposit)
    assert isinstance(example, NectarDeposit)

    wire = example.model_dump(mode="json")

    assert isinstance(wire["chunk"], str)
    assert NectarDeposit.model_validate(wire).chunk == CONTENT
    assert _rebuild(example, chunk=b"\x00" * MAX_CHUNK_BYTES)
    with pytest.raises(ValidationError, match=f"at most {MAX_CHUNK_BYTES}"):
        _rebuild(example, chunk=b"\x00" * (MAX_CHUNK_BYTES + 1))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"sha256": SHA256.upper()}, "pattern"),
        ({"sha256": SHA256[:-1]}, "pattern"),
        ({"media_type": ""}, "at least 1"),
        ({"media_type": "x" * (MAX_MEDIA_TYPE_CHARS + 1)}, f"at most {MAX_MEDIA_TYPE_CHARS}"),
        ({"title": "x" * (MAX_TITLE_CHARS + 1)}, f"at most {MAX_TITLE_CHARS}"),
        ({"offset": -1}, "greater than or equal to 0"),
        ({"total_bytes": 0}, "greater than or equal to 1"),
        ({"observed_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
        ({"task_id": new_id(IdKind.CELL, CLOCK)}, "task_"),
        ({"cell_id": new_id(IdKind.TASK, CLOCK)}, "cell_"),
        ({"worker_id": new_id(IdKind.WARDEN, CLOCK)}, "worker_"),
        ({"kind": "HANDOFF", "event_id": new_id(IdKind.TASK, CLOCK)}, "event_"),
    ],
)
def test_nectar_deposit_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(NectarDeposit), **changes)


@pytest.mark.parametrize(
    ("kind", "origin_tier", "clearance"),
    [
        ("RIPENED_HONEY", "NIGHT_VEIL", "C0"),
        ("RIPENED_HONEY", "NIGHT_VEIL", "C1"),
        ("RIPENED_HONEY", "MEADOW", "C2"),
        ("FINDING", "NIGHT_VEIL", "C2"),
    ],
)
def test_nectar_deposit_accepts_night_veil_ripened_honey_up_to_c1(
    kind: str, origin_tier: str, clearance: str
) -> None:
    deposit = _rebuild(
        _example(NectarDeposit), kind=kind, origin_tier=origin_tier, clearance=clearance
    )

    assert isinstance(deposit, NectarDeposit)
    assert deposit.clearance is HoneyClearance(clearance)


def test_nectar_deposit_rejects_night_veil_ripened_honey_at_c2() -> None:
    with pytest.raises(ValidationError, match="must be labelled C0 or C1"):
        _rebuild(
            _example(NectarDeposit), kind="RIPENED_HONEY", origin_tier="NIGHT_VEIL", clearance="C2"
        )


def test_nectar_deposit_event_id_is_set_exactly_for_a_handoff() -> None:
    example = _example(NectarDeposit)
    event_id = new_id(IdKind.EVENT, CLOCK)

    handoff = _rebuild(example, kind="HANDOFF", event_id=event_id)

    assert isinstance(handoff, NectarDeposit)
    assert handoff.event_id == event_id
    with pytest.raises(ValidationError, match="exactly when kind is HANDOFF"):
        _rebuild(example, kind="HANDOFF", event_id=None)
    with pytest.raises(ValidationError, match="exactly when kind is HANDOFF"):
        _rebuild(example, kind="FINDING", event_id=event_id)


def test_nectar_deposit_accepts_a_warden_deposit_and_a_patrol_summary() -> None:
    deposit = _rebuild(_example(NectarDeposit), kind="PATROL_SUMMARY", worker_id=None, task_id=None)

    assert isinstance(deposit, NectarDeposit)
    assert deposit.worker_id is None
    assert deposit.task_id is None


# ──────────────────────────────────────────────────────────────────────────────
# HoneyQuery
# ──────────────────────────────────────────────────────────────────────────────


def test_honey_query_defaults_max_hits_and_bounds_it() -> None:
    example = _example(HoneyQuery)
    assert isinstance(example, HoneyQuery)
    assert example.max_hits == DEFAULT_MAX_HITS
    assert _rebuild(example, max_hits=MAX_MAX_HITS)
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _rebuild(example, max_hits=0)
    with pytest.raises(ValidationError, match=f"less than or equal to {MAX_MAX_HITS}"):
        _rebuild(example, max_hits=MAX_MAX_HITS + 1)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"text": ""}, "at least 1"),
        ({"text": "x" * (MAX_QUERY_CHARS + 1)}, f"at most {MAX_QUERY_CHARS}"),
        ({"scopes": ("hive",) * (MAX_SCOPES + 1)}, f"at most {MAX_SCOPES}"),
        ({"scopes": ("cell:" + "x" * MAX_SCOPE_CHARS,)}, f"at most {MAX_SCOPE_CHARS}"),
        ({"max_tokens": 0}, "greater than or equal to 1"),
        ({"requester": new_id(IdKind.CELL, CLOCK)}, "worker_, warden_"),
        ({"task_id": new_id(IdKind.WORKER, CLOCK)}, "task_"),
    ],
)
def test_honey_query_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(HoneyQuery), **changes)


@pytest.mark.parametrize("scope", ["hive", "cell:cell_x", "bee:worker_x", "task:task_x"])
def test_honey_query_accepts_every_scope_shape(scope: str) -> None:
    query = _rebuild(_example(HoneyQuery), scopes=(scope,))

    assert isinstance(query, HoneyQuery)
    assert query.scopes == (scope,)


@pytest.mark.parametrize("scope", ["", "cell:", "hive:x", "bee:a:b", "task", "Hive", "node:x"])
def test_honey_query_rejects_a_scope_outside_the_pattern(scope: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _rebuild(_example(HoneyQuery), scopes=(scope,))


def test_honey_query_accepts_a_warden_requester_and_no_scopes() -> None:
    query = _rebuild(_example(HoneyQuery), requester=new_id(IdKind.WARDEN, CLOCK), scopes=())

    assert isinstance(query, HoneyQuery)
    assert query.scopes == ()


# ──────────────────────────────────────────────────────────────────────────────
# HoneyResponse, HoneyHit, HoneyProvenance
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"hits": (HIT,) * (MAX_MAX_HITS + 1)}, f"at most {MAX_MAX_HITS}"),
        ({"token_count": -1}, "greater than or equal to 0"),
        ({"filtered_count": -1}, "greater than or equal to 0"),
    ],
)
def test_honey_response_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(HoneyResponse), **changes)


def test_honey_response_accepts_no_hits() -> None:
    empty = _rebuild(_example(HoneyResponse), hits=(), token_count=0, filtered_count=0)

    assert empty.model_dump(mode="json")["hits"] == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"honey_ref": "x" * (MAX_HONEY_REF_CHARS + 1)}, f"at most {MAX_HONEY_REF_CHARS}"),
        ({"title": "x" * (MAX_HIT_TITLE_CHARS + 1)}, f"at most {MAX_HIT_TITLE_CHARS}"),
        ({"excerpt": "x" * (MAX_EXCERPT_CHARS + 1)}, f"at most {MAX_EXCERPT_CHARS}"),
        ({"score": 1.01}, "less than or equal to 1"),
        ({"score": -0.01}, "greater than or equal to 0"),
        ({"scope": "hive:x"}, "pattern"),
        ({"scope": "cell:" + "x" * MAX_SCOPE_CHARS}, f"at most {MAX_SCOPE_CHARS}"),
    ],
)
def test_honey_hit_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(HIT, **changes)


def test_honey_hit_accepts_the_score_extremes_and_a_night_veil_origin() -> None:
    assert _rebuild(HIT, score=0.0, origin_tier="NIGHT_VEIL")
    assert _rebuild(HIT, score=1.0).model_dump()["score"] == 1.0


def test_honey_provenance_accepts_a_warden_bee_and_unknown_origins() -> None:
    warden = _rebuild(PROVENANCE, bee=new_id(IdKind.WARDEN, CLOCK))
    unknown = _rebuild(PROVENANCE, task_id=None, cell_id=None, bee=None)

    assert isinstance(warden, HoneyProvenance)
    assert warden.bee is not None
    assert warden.bee.startswith("warden_")
    assert unknown.model_dump()["bee"] is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"bee": new_id(IdKind.CELL, CLOCK)}, "worker_, warden_"),
        ({"task_id": new_id(IdKind.CELL, CLOCK)}, "task_"),
        ({"cell_id": new_id(IdKind.TASK, CLOCK)}, "cell_"),
        ({"observed_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
    ],
)
def test_honey_provenance_rejects_a_wrong_id_kind_or_a_naive_time(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(PROVENANCE, **changes)
