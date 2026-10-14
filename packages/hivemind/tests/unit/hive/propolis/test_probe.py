"""Tests for hivemind.hive.propolis.probe: PropolisProbe and the names of its checks.

Fits into the Hive:
    Mirrors src/hivemind/hive/propolis/probe.py (codingrules section 3). The Protocol has one
    method, so what needs holding in place is the list of check names a complete probe reports:
    the Real Cell's four, which this tier takes from `hivemind.cell.tunnel` and must never
    rename, and Tor's.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.propolis.probe for the module under test.
    - hivemind.swarm.shield for the Real Cell checks these names are shared with.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import FakeSession, OsFamily
from hivemind.hive.propolis import (
    CHECK_TOR,
    PROPOLIS_CHECK_NAMES,
    FakePropolisProbe,
    PropolisProbe,
    SessionPropolisProbe,
)
from hivemind.swarm.shield import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    judge_all,
)
from waggle.clock import FakeClock


def test_the_names_are_the_real_cell_s_four_in_their_order_and_then_tor() -> None:
    # judge_all is what a Real Cell is verified by; the Virtual Cell's names are its names.
    real_cell_names = tuple(check.name for check in judge_all((), ()).checks)

    assert (*real_cell_names, CHECK_TOR) == PROPOLIS_CHECK_NAMES
    assert PROPOLIS_CHECK_NAMES[:4] == (CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_IPV6, CHECK_EGRESS)


def test_every_name_is_unique() -> None:
    assert len(set(PROPOLIS_CHECK_NAMES)) == len(PROPOLIS_CHECK_NAMES)


async def test_both_implementations_are_interchangeable_behind_the_protocol() -> None:
    session = FakeSession(Path("/scratch"), FakeClock())  # Every command: not found (exit 127).
    probes: list[PropolisProbe] = [
        FakePropolisProbe(),
        SessionPropolisProbe(session, OsFamily.LINUX),
    ]

    for probe in probes:
        assert set(PROPOLIS_CHECK_NAMES) <= set(await probe.checks())
