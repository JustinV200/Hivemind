"""Tests for hivemind.manifest.schema.forage: ForageSection and its role-key validator.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/forage.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.forage for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.forage import ForageSection

_DRONE_FOOTPRINT = {"cpu_cores": 0.5, "memory_bytes": 1024, "token_rate_per_minute": 100.0}


def test_forage_section_has_sensible_ceiling_defaults() -> None:
    section = ForageSection(roles={"drone": _DRONE_FOOTPRINT})

    assert section.grant_ttl_s == 300.0
    assert section.spend_cap_per_goal_usd == 5.0
    assert section.token_budget_per_goal == 2_000_000
    assert section.max_sub_bees_per_goal == 4


def test_forage_section_requires_a_drone_role() -> None:
    with pytest.raises(ValidationError, match="drone"):
        ForageSection()


def test_forage_section_rejects_an_unknown_role_key() -> None:
    with pytest.raises(ValidationError, match="not_a_role"):
        ForageSection(roles={"drone": _DRONE_FOOTPRINT, "not_a_role": _DRONE_FOOTPRINT})


def test_forage_section_accepts_every_worker_role_key() -> None:
    roles = {
        "drone": _DRONE_FOOTPRINT,
        "forager": _DRONE_FOOTPRINT,
        "scout": _DRONE_FOOTPRINT,
        "guard_bee": _DRONE_FOOTPRINT,
        "undertaker": _DRONE_FOOTPRINT,
        "house_bee": _DRONE_FOOTPRINT,
    }

    section = ForageSection(roles=roles)

    assert set(section.roles) == set(roles)


def test_forage_section_map_and_reserve_default_to_empty_and_sensible() -> None:
    section = ForageSection(roles={"drone": _DRONE_FOOTPRINT})

    assert section.map == {}
    assert section.reserve.seats == 1


def test_forage_section_is_frozen_and_forbids_extras() -> None:
    section = ForageSection(roles={"drone": _DRONE_FOOTPRINT})

    with pytest.raises(ValidationError, match="frozen"):
        section.grant_ttl_s = 10.0  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ForageSection.model_validate({**section.model_dump(), "nope": 1})
