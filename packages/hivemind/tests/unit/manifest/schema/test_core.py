"""Tests for hivemind.manifest.schema.core: HiveSection through PheromoneSection.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/core.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.core for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.core import (
    BroodChamberSection,
    HiveSection,
    HiveStandCapacityOverrides,
    HiveStandSection,
    PheromoneSection,
    QueenSection,
)
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_CLOCK = FakeClock()


def _hive_ids() -> dict[str, str]:
    return {"id": new_hive_id(_CLOCK), "node_id": new_node_id(_CLOCK)}


def test_hive_section_accepts_only_id_and_node_id_as_required() -> None:
    section = HiveSection(**_hive_ids())

    assert section.name == ""
    assert section.env == "dev"
    assert str(section.db) == "hive.sqlite3"


def test_hive_section_requires_id_and_node_id() -> None:
    with pytest.raises(ValidationError):
        HiveSection()  # type: ignore[call-arg]


def test_hive_section_rejects_a_malformed_id() -> None:
    with pytest.raises(ValidationError):
        HiveSection(id="not-an-id", node_id=new_node_id(_CLOCK))


def test_hive_section_rejects_an_unknown_env() -> None:
    with pytest.raises(ValidationError):
        HiveSection(**_hive_ids(), env="staging")


def test_hive_section_is_frozen_and_forbids_extras() -> None:
    section = HiveSection(**_hive_ids())

    with pytest.raises(ValidationError, match="frozen"):
        section.name = "renamed"  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        HiveSection.model_validate({**section.model_dump(), "nope": 1})


def test_queen_section_has_sensible_defaults() -> None:
    section = QueenSection()

    assert section.tick_interval_s == 0.25
    assert section.heartbeat_interval_s == 5.0
    assert section.max_awake_per_minute == 30


def test_queen_section_rejects_a_non_positive_tick_interval() -> None:
    with pytest.raises(ValidationError):
        QueenSection(tick_interval_s=0)


def test_hive_stand_capacity_overrides_default_to_none() -> None:
    overrides = HiveStandCapacityOverrides()

    assert overrides.max_sub_bees is None
    assert overrides.cores is None
    assert overrides.memory_bytes is None


def test_hive_stand_section_defaults_to_a_loopback_address() -> None:
    section = HiveStandSection()

    assert section.enabled is True
    assert section.address == "ws://127.0.0.1:8720"


def test_hive_stand_section_accepts_a_wss_address_anywhere() -> None:
    section = HiveStandSection(address="wss://hive.example.org:8443/waggle")

    assert section.address == "wss://hive.example.org:8443/waggle"


def test_hive_stand_section_rejects_a_non_loopback_ws_address() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        HiveStandSection(address="ws://hive.example.org:8720")


def test_hive_stand_section_rejects_a_non_waggle_uri() -> None:
    with pytest.raises(ValidationError):
        HiveStandSection(address="http://127.0.0.1:8720")


def test_brood_chamber_section_has_a_sensible_default() -> None:
    assert BroodChamberSection().max_graph_tasks == 64


def test_pheromone_section_has_a_sensible_default() -> None:
    assert PheromoneSection().retention_days == 90
