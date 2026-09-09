"""Build valid hivemind.forage test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about. `make_source` accepts overrides for `ModelSourceSpec`'s own
fields (`grade`, `cost`, `provider`, ...) directly, alongside the top-level `ModelSource` fields
(`source_id`, `distance`, `abundance`), so a test that only cares about grade writes
`make_source(grade=4)` rather than building a nested spec by hand.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/forage.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic.
    - Every builder's result passes the model's own validators with no further overrides needed.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.forage.models for the models these builders build.
    - packages/hivemind/tests/builders/tasks.py for the sibling builder module this one matches
      in style.
"""

from __future__ import annotations

from datetime import timedelta

from hivemind.forage.grant_state import GrantState
from hivemind.forage.models import (
    Abundance,
    ForageCapacity,
    ForageGrant,
    HostCapacity,
    ModelCost,
    ModelSource,
    ModelSourceSpec,
    RoleFootprint,
    RoyalReserve,
)
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_warden_id
from waggle.messages.labels import OsFamily

_GIB = 1024**3  # A gibibyte, for readable memory/disk defaults below.
_MIB = 1024**2  # A mebibyte, for readable footprint memory defaults.
_DEFAULT_GRANT_TTL_S = 300.0  # Five minutes: a plausible, arbitrary grant lifetime for tests.

# ModelSourceSpec's own field names, so make_source can route an override to the nested spec
# instead of to ModelSource's own top-level fields without the caller naming which is which.
_SPEC_FIELD_NAMES = frozenset(
    {
        "provider",
        "model",
        "grade",
        "context_window",
        "cost",
        "capabilities",
        "seats",
        "host_cell_id",
    }
)

__all__ = [
    "make_capacity",
    "make_footprint",
    "make_grant",
    "make_host_capacity",
    "make_reserve",
    "make_source",
]


def make_host_capacity(**overrides: object) -> HostCapacity:
    """Build a valid HostCapacity: a modest, otherwise-idle host.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated HostCapacity.
    """
    fields: dict[str, object] = {
        "cores": 8,
        "memory_bytes": 16 * _GIB,
        "memory_free_bytes": 8 * _GIB,
        "disk_bytes": 100 * _GIB,
        "disk_free_bytes": 50 * _GIB,
        "cpu_load": 0.2,
        "gpus": (),
        "arch": "x86_64",
        "os": OsFamily.LINUX,
    }
    fields.update(overrides)
    return HostCapacity(**fields)


def make_footprint(**overrides: object) -> RoleFootprint:
    """Build a valid RoleFootprint: a light, single-seat role.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated RoleFootprint.
    """
    fields: dict[str, object] = {
        "cpu_cores": 1.0,
        "memory_bytes": 512 * _MIB,
        "seats": 1,
        "token_rate_per_minute": 1_000.0,
        "exoskeleton_extra_memory_bytes": 0,
    }
    fields.update(overrides)
    return RoleFootprint(**fields)


def make_source(**overrides: object) -> ModelSource:
    """Build a valid ModelSource, routing spec-field overrides to its nested ModelSourceSpec.

    Args:
        **overrides: Field values that replace the defaults below. A name from
            ModelSourceSpec's own fields (`grade`, `cost`, `provider`, ...) replaces that field on
            the built spec; anything else (`source_id`, `distance`, `abundance`, or a whole
            replacement `spec`) replaces the corresponding ModelSource field directly.

    Returns:
        A validated ModelSource with 4 free seats and no measured distance yet.
    """
    spec_fields: dict[str, object] = {
        "provider": "test-provider",
        "model": "test-model",
        "grade": 3,
        "context_window": 8_192,
        "cost": ModelCost(),
        "capabilities": (),
        "seats": 1,
        "host_cell_id": None,
    }
    top_fields: dict[str, object] = {
        "source_id": "test-source",
        "distance": None,
        "abundance": Abundance(
            seats_free=4, requests_per_minute_left=None, tokens_per_minute_left=None
        ),
    }
    # Route each override to the spec or to the top level by name, so a caller need not know
    # ModelSource is split into a static spec and a live half.
    for key, value in overrides.items():
        if key in _SPEC_FIELD_NAMES:
            spec_fields[key] = value
        else:
            top_fields[key] = value
    top_fields.setdefault("spec", ModelSourceSpec(**spec_fields))
    return ModelSource(**top_fields)


def make_capacity(**overrides: object) -> ForageCapacity:
    """Build a valid ForageCapacity: a modest host with no local model servers.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated ForageCapacity.
    """
    fields: dict[str, object] = {
        "host": make_host_capacity(),
        "local_seats": (),
        "max_sub_bees": 4,
    }
    fields.update(overrides)
    return ForageCapacity(**fields)


def make_reserve(**overrides: object) -> RoyalReserve:
    """Build a valid RoyalReserve, from its own defaults unless overridden.

    Args:
        **overrides: Field values that replace RoyalReserve's own defaults.

    Returns:
        A validated RoyalReserve.
    """
    return RoyalReserve(**overrides)


def make_grant(
    state: GrantState = GrantState.ISSUED, clock: Clock | None = None, **overrides: object
) -> ForageGrant:
    """Build a valid ForageGrant in `state`, with two free sub-bees and no bindings.

    Args:
        state: The grant's GrantState; ISSUED by default.
        clock: Source of every minted id and timestamp; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `state` itself.

    Returns:
        A validated ForageGrant.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_grant_id(active_clock),
        "holder": new_warden_id(active_clock),
        "cell_id": new_cell_id(active_clock),
        "task_id": None,
        "revision": 0,
        "allowed": (),
        "seats": (),
        "token_budget": 100_000,
        "spend_budget": 10.0,
        "tokens_spent": 0,
        "spent": 0.0,
        "max_sub_bees": 2,
        "expires_at": active_clock.now() + timedelta(seconds=_DEFAULT_GRANT_TTL_S),
        "reason": "built for a test",
        "state": state,
    }
    fields.update(overrides)
    return ForageGrant(**fields)
