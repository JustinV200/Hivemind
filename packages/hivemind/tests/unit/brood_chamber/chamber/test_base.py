"""Tests for hivemind.brood_chamber.chamber.base: ChamberIdentity.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/base.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). `_ChamberBase` itself (the store/clock/identity plumbing every mixin shares)
    is private and has no behaviour beyond what every test in this test package already exercises
    through BroodChamber's public methods (chamber/test_submission.py, test_lifecycle.py,
    test_outcomes.py, test_questions.py, test_queries.py); this file directly tests
    ChamberIdentity, the one public name base.py defines that none of those exercise on its own.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.base for the module under test.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hivemind.brood_chamber.chamber import ChamberIdentity
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


def test_chamber_identity_holds_hive_node_and_actor() -> None:
    clock = FakeClock()
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)

    identity = ChamberIdentity(hive_id=hive_id, node_id=node_id, actor="system")

    assert identity.hive_id == hive_id
    assert identity.node_id == node_id
    assert identity.actor == "system"


def test_chamber_identity_is_frozen() -> None:
    clock = FakeClock()
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )

    with pytest.raises(FrozenInstanceError):
        identity.actor = "human"  # type: ignore[misc]  # deliberately mutating a frozen dataclass
