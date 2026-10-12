"""Build the Observation Hive's pure-builder inputs: trail events, Cell, Warden and bee views.

The aggregated screens in ``hivemind.observation.api`` are pure functions over records, so their
tests need those records without a Hive behind them: a trail event of any kind at a chosen instant,
an ``llm.call`` event with its usage, a Cell view, a Warden view and one bee's telemetry sample.
Every builder fills a valid default and takes keyword overrides; every instant counts from ``T0`` so
"older" and "newer" are a ``timedelta`` away.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/observation and .../unit/entrance/reads/screens.

Key invariants:
    - Builders return real, validated models; they never bypass validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from builders.cells import make_cell
from pydantic import JsonValue

from hivemind.cell import AccessLevel, CellKind, CombShieldLevel
from hivemind.observation import CellView, TelemetrySampleView, WardenView
from hivemind.pheromone import LlmEvent, LlmUsage, PheromoneEvent, event_class_for
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages.base import UtcDatetime
from waggle.messages.supervision import WardenState

T0 = datetime(2026, 1, 1, tzinfo=UTC)  # Every built instant counts from here.
_CLOCK = FakeClock()  # Only mints ids: a built event's time is always the caller's.

__all__ = [
    "T0",
    "after",
    "make_cell_view",
    "make_event",
    "make_llm_call",
    "make_sample",
    "make_warden_view",
]


def after(seconds: float) -> UtcDatetime:
    """Return the instant ``seconds`` after ``T0``."""
    return T0 + timedelta(seconds=seconds)


def make_event(
    kind: str,
    subject_id: str,
    at: datetime | None = None,
    *,
    actor: str = "system",
    payload: Mapping[str, JsonValue] | None = None,
) -> PheromoneEvent:
    """Build one trail event of ``kind`` about ``subject_id``.

    Args:
        kind: Any kind of any family.
        subject_id: What it is about.
        at: When it happened; ``T0`` when omitted.
        actor: Who did it: an id, or ``human`` / ``system``.
        payload: Its payload; empty when omitted.

    Returns:
        The event, of its family's class.
    """
    return event_class_for(kind)(
        id=new_event_id(_CLOCK),
        hive_id=new_hive_id(_CLOCK),
        node_id=new_node_id(_CLOCK),
        at=at if at is not None else T0,
        actor=actor,
        kind=kind,
        subject_id=subject_id,
        payload=dict(payload) if payload is not None else {},
    )


def make_llm_call(
    at: datetime | None = None,
    slot: str = "WORKER",
    provider: str = "cloud",
    cost_usd: float = 0.5,
    lane: Mapping[str, JsonValue] | None = None,
) -> LlmEvent:
    """Build one completed ``llm.call`` event with its normalised usage (100 in, 40 out).

    Args:
        at: When it happened; ``T0`` when omitted.
        slot: The model slot it served.
        provider: The provider's manifest name.
        cost_usd: What it cost.
        lane: What its Fanner lane carried beside the usage: ``goal_id`` and/or ``grant_id``.

    Returns:
        The event.
    """
    return LlmEvent(
        id=new_event_id(_CLOCK),
        hive_id=new_hive_id(_CLOCK),
        node_id=new_node_id(_CLOCK),
        at=at if at is not None else T0,
        actor="system",
        kind="llm.call",
        subject_id=new_event_id(_CLOCK),
        slot=slot,
        provider=provider,
        usage=LlmUsage(input_tokens=100, output_tokens=40, cached_tokens=0, cost_usd=cost_usd),
        payload=dict(lane) if lane is not None else {},
    )


def make_cell_view(**overrides: object) -> CellView:
    """Build a valid CellView of a Real, MEADOW, SCRATCH-access Cell with nothing known beyond it.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated CellView.
    """
    fields: dict[str, object] = {
        "id": make_cell(clock=_CLOCK).id,
        "name": "test-cell",
        "kind": CellKind.REAL,
        "source": "test-source",
        "comb_shield": CombShieldLevel.MEADOW,
        "access_level": AccessLevel.SCRATCH,
        "mode": None,
        "mask_state": None,
        "lease_state": None,
        "virtual_status": None,
        "last_event": None,
        "last_event_at": None,
        "warden_id": None,
        "current_tasks": [],
        "os": None,
        "has_display": False,
        "has_browser": False,
        "can_host_model": False,
    }
    fields.update(overrides)
    return CellView(**fields)


def make_warden_view(**overrides: object) -> WardenView:
    """Build a valid WardenView of an ACTIVE, online Warden with no sub-bees or grants.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated WardenView.
    """
    fields: dict[str, object] = {
        "id": new_warden_id(_CLOCK),
        "cell_id": make_cell(clock=_CLOCK).id,
        "state": WardenState.ACTIVE,
        "offline": False,
        "last_heartbeat_at": T0,
        "missed_heartbeats": 0,
        "sub_bees": 0,
        "live_grants": 0,
    }
    fields.update(overrides)
    return WardenView(**fields)


def make_sample(warden_id: str, **overrides: object) -> TelemetrySampleView:
    """Build a valid telemetry sample of a Warden's own report (set ``worker_id`` for a sub-bee).

    Args:
        warden_id: The Warden whose Heartbeat carried it.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TelemetrySampleView.
    """
    fields: dict[str, object] = {
        "warden_id": warden_id,
        "worker_id": None,
        "task_id": None,
        "warden_state": WardenState.ACTIVE,
        "worker_state": None,
        "tokens_used": 2_048,
        "context_window": 8_192,
        "goal": "Do the thing.",
        "last_actions": [],
        "blockers": [],
        "spend": 0.25,
        "at": T0,
    }
    fields.update(overrides)
    return TelemetrySampleView(**fields)
