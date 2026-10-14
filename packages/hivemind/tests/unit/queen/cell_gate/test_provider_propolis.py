"""Tests for hivemind.queen.cell_gate.provider's attestation of a PROPOLIS Virtual Cell.

Split by feature from test_provider.py (codingrules 5.1). The provider is the real one, over a real
`CellLifecycle`, `QueenReadinessGate` and `CellAttestation`; only the backend's dial-back and the
PROPOLIS probe are stood in (`builders.attested_cells`). A freshly provisioned PROPOLIS Cell is
ready only after a passed attestation, is torn down after a red one or when the probe cannot run,
and is refused outright when no probe is configured; MEADOW stays unattested and NIGHT_VEIL is
attested as it always was, now naming its tier.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/provider.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.provider for the class under test.
    - tests/e2e/test_virtual_cells_propolis.py for the same contract through a running Queen.
"""

from __future__ import annotations

import pytest
from builders.attested_cells import ProviderRig, make_provider_rig, make_tier_spec

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CombShieldLevel
from hivemind.common.errors import ConfigurationError
from hivemind.hive import CellProvisionError
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis import CHECK_TOR, PROPOLIS_CHECK_NAMES, FakePropolisProbe, PropolisProbe
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.placement import ProvisionVirtual

_RED = CheckResult(status=CheckStatus.FAIL, detail="a process named tor is running")


def _task() -> Task:
    """A Task stand-in; `LifecycleVirtualCellProvider.acquire` never reads it for these tiers."""
    return None  # type: ignore[return-value]


def _green(_cell: Cell) -> PropolisProbe:
    """A PROPOLIS probe factory whose Cells pass every check."""
    return FakePropolisProbe()


def _red(_cell: Cell) -> PropolisProbe:
    """A PROPOLIS probe factory whose Cells fail the Tor check."""
    return FakePropolisProbe({CHECK_TOR: _RED})


def _placement(tier: CombShieldLevel = CombShieldLevel.PROPOLIS) -> ProvisionVirtual:
    return ProvisionVirtual(spec=make_tier_spec(tier), backend="fake", reason="test")


async def _kinds(rig: ProviderRig) -> list[str]:
    """Every event kind on the rig's trail, in the order it was recorded."""
    return [event.kind for event in await rig.trail.query(TrailQuery())]


async def _attested(rig: ProviderRig) -> list[PheromoneEvent]:
    return list(await rig.trail.query(TrailQuery(kind="cell.attested")))


def _is_gone(rig: ProviderRig) -> bool:
    """Whether the rig's lifecycle tracks no Cell any more (or only a destroyed record)."""
    return [cell.status for cell in rig.lifecycle.live_cells()] in (
        [],
        [VirtualCellStatus.DESTROYED],
    )


async def test_a_propolis_cell_is_ready_only_after_a_passed_attestation() -> None:
    rig = make_provider_rig(propolis=_green)

    link = await rig.provider.acquire(_placement(), _task())

    assert link in rig.wardens
    assert rig.lifecycle.status_of(link.cell.id) is VirtualCellStatus.READY
    kinds = await _kinds(rig)
    assert kinds.index("cell.attested") < kinds.index("cell.ready")
    [event] = await _attested(rig)
    assert event.subject_id == link.cell.id
    assert event.payload["passed"] is True
    assert event.payload["comb_shield"] == "PROPOLIS"
    assert set(PROPOLIS_CHECK_NAMES) <= set(event.payload)


async def test_a_red_check_tears_the_cell_down_names_it_and_is_recorded_per_check() -> None:
    rig = make_provider_rig(propolis=_red)

    with pytest.raises(CellProvisionError, match=CHECK_TOR) as refused:
        await rig.provider.acquire(_placement(), _task())

    assert "PROPOLIS attestation failed" in str(refused.value)
    kinds = await _kinds(rig)
    assert "cell.ready" not in kinds  # It was never marked ready.
    assert kinds.index("cell.attested") < kinds.index("cell.destroyed")
    [event] = await _attested(rig)
    assert event.payload["passed"] is False
    assert event.payload["red"] == [CHECK_TOR]
    assert event.payload[CHECK_TOR] == {"status": "FAIL", "detail": _RED.detail}
    assert _is_gone(rig)


@pytest.mark.parametrize(
    "failure",
    [
        NotImplementedError("no real probe wired up"),
        ConfigurationError("no session path"),
        OSError("link lost"),
        LookupError("no link"),
        ValueError("a result the models refused"),
    ],
    ids=lambda failure: type(failure).__name__,
)
async def test_a_probe_that_cannot_run_tears_the_cell_down_and_refuses_it(
    failure: Exception,
) -> None:
    def broken(_cell: Cell) -> PropolisProbe:
        raise failure

    rig = make_provider_rig(propolis=broken)

    with pytest.raises(CellProvisionError, match="PROPOLIS attestation could not run"):
        await rig.provider.acquire(_placement(), _task())

    assert "cell.ready" not in await _kinds(rig)
    assert _is_gone(rig)


async def test_a_failure_nobody_expected_still_tears_the_cell_down_before_it_escapes() -> None:
    def buggy(_cell: Cell) -> PropolisProbe:
        raise RuntimeError("a bug in a probe factory")

    rig = make_provider_rig(propolis=buggy)

    with pytest.raises(RuntimeError, match="a bug"):
        await rig.provider.acquire(_placement(), _task())

    assert "cell.ready" not in await _kinds(rig)
    assert _is_gone(rig)  # Never left PROVISIONING for a sweep to find.


async def test_with_no_propolis_probe_configured_no_propolis_cell_is_ready() -> None:
    rig = make_provider_rig()  # The tier is left unconfigured, as production leaves it.

    with pytest.raises(CellProvisionError, match="no PROPOLIS attestation probe"):
        await rig.provider.acquire(_placement(), _task())

    assert "cell.ready" not in await _kinds(rig)
    assert await _attested(rig) == []  # Nothing was looked at, so nothing is said.
    assert _is_gone(rig)


async def test_a_meadow_cell_is_still_not_attested() -> None:
    rig = make_provider_rig(propolis=_red)  # A probe that would fail it, were it asked.

    link = await rig.provider.acquire(_placement(CombShieldLevel.MEADOW), _task())

    assert rig.lifecycle.status_of(link.cell.id) is VirtualCellStatus.READY
    assert await _attested(rig) == []


async def test_a_night_veil_cell_is_still_attested_by_its_own_probe_and_names_its_tier() -> None:
    rig = make_provider_rig(propolis=_red)  # Its Propolis probe is never asked for this Cell.

    link = await rig.provider.acquire(_placement(CombShieldLevel.NIGHT_VEIL), _task())

    assert rig.lifecycle.status_of(link.cell.id) is VirtualCellStatus.READY
    [event] = await _attested(rig)
    assert event.payload["comb_shield"] == "NIGHT_VEIL"
    assert "kill_switch_active" in event.payload


async def test_a_propolis_cell_announcing_another_tier_is_refused_before_it_is_attested() -> None:
    rig = make_provider_rig(propolis=_green, announce=CombShieldLevel.MEADOW)

    with pytest.raises(CellProvisionError, match="announced Comb Shield tier MEADOW"):
        await rig.provider.acquire(_placement(), _task())

    assert await _attested(rig) == []
    assert _is_gone(rig)
