"""Tests for hivemind.entrance.auth.travel.fake: peers placed by hand.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/travel/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.travel.fake for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.auth import FakePeerEndpointSource


async def test_the_fake_answers_what_was_last_placed_and_records_what_was_asked() -> None:
    source = FakePeerEndpointSource({"100.64.0.7": "203.0.113.0/24"})
    source.place("100.64.0.7", "derp:nyc")

    placed = await source.current_network("100.64.0.7")
    unknown = await source.current_network("100.64.0.8")

    assert (placed, unknown) == ("derp:nyc", None)
    assert source.asked == ["100.64.0.7", "100.64.0.8"]


def test_the_fake_refuses_a_network_the_real_source_would_never_report() -> None:
    with pytest.raises(ValueError, match="canonical"):
        FakePeerEndpointSource({"100.64.0.7": "203.0.113.54/24"})
