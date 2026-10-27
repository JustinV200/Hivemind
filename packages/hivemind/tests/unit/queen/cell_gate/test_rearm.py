"""Tests for hivemind.queen.cell_gate.rearm: a restarted Queen's gate learns the live Cells' keys.

A Cell provisioned by one Queen carries its public key in a backend label; a second Queen's
fresh gate, re-armed from the labels, expects that very key, so the Cell's Warden can prove itself
to her. A Cell this process already expects keeps the key it was provisioned with, and a label
that is no Ed25519 public key registers nothing.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/rearm.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.rearm for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity

from hivemind.hive import CELL_KEY_LABEL, BackendRegistry, FakeCellBackend, VirtualCellSpec
from hivemind.hive.backends import QueenEndpoint
from hivemind.queen.cell_gate import QueenReadinessGate, rearm_gate
from waggle.clock import FakeClock
from waggle.ids import CellId, HiveId, new_hive_id, new_node_id

_CLOCK = FakeClock()
_BAD_KEY = "not-a-key"  # What a damaged or foreign label might read.


async def _provisioned(hive_id: HiveId, bad_label: bool) -> tuple[BackendRegistry, CellId, str]:
    """A backend holding one Cell a first Queen provisioned, and the key its gate was told.

    With `bad_label`, the Cell is one no bootstrap was minted for, its key label damaged.
    """
    first_gate = QueenReadinessGate()
    endpoint = QueenEndpoint(
        waggle_url="ws://127.0.0.1:8711",
        queen_node_id=new_node_id(_CLOCK),
        queen_verify_key_hex="00" * 32,
    )
    backend = (
        FakeCellBackend(_CLOCK)
        if bad_label
        else FakeCellBackend(_CLOCK, endpoint=endpoint, gate=first_gate)
    )
    cell = await backend.provision(_spec(hive_id, {CELL_KEY_LABEL: _BAD_KEY} if bad_label else {}))
    registry = BackendRegistry()
    registry.register("fake", lambda: backend)
    return registry, cell.id, first_gate.expected_key(cell.id) or _BAD_KEY


@pytest.mark.parametrize(
    ("bad_label", "already", "expects"),
    [
        (False, None, "minted"),  # A Cell that outlived the last Queen: its key is re-armed.
        (False, "ab" * 32, "ab" * 32),  # One this process expects already: left as it is.
        (True, None, None),  # A label that is no public key: nothing is registered.
    ],
)
async def test_a_restarted_queens_gate_expects_the_key_each_live_cell_carries(
    bad_label: bool, already: str | None, expects: str | None
) -> None:
    hive_id = new_hive_id(_CLOCK)
    registry, cell_id, minted = await _provisioned(hive_id, bad_label)
    gate = QueenReadinessGate()  # The restarted Queen's, empty.
    if already is not None:
        await gate.expect(cell_id, already)

    listed = await rearm_gate(gate, registry, hive_id)

    assert [record.cell_id for record in listed] == [cell_id]
    assert gate.expected_key(cell_id) == (minted if expects == "minted" else expects)


def _spec(hive_id: HiveId, labels: dict[str, str]) -> VirtualCellSpec:
    """A small Meadow Cell's spec for `hive_id`, with `labels`."""
    return VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        capacity=make_capacity(),
        hive_id=hive_id,
        ready_timeout_s=5.0,
        labels=labels,
    )
