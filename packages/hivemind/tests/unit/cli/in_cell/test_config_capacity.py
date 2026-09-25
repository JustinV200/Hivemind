"""Tests for hivemind.cli.in_cell.config's capacity: a Virtual Cell reports its own reservation.

A container shares its host's kernel, so a Cell that probed itself read the host's cores, memory
and load average: on a busy Hive Stand every Virtual Cell looked just as busy and its grants
shrank to nothing. The Cell now reports the reservation its bootstrap names (from its
`VirtualCellSpec`), with no load; only its platform facts come from the probe. Each test walks
the real path: the Queen's own spec template, the bootstrap a backend mints from it, and the
Cell's own reading of that environment.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/config.py (codingrules 5.1: split by feature from
    test_config.py and test_config_tier.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.models for CellReservation, the one computation both sides share.
    - hivemind.hive.backends.bootstrap for cell_endpoint, which ships the reservation.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from hivemind.cli.compose.virtual_cells import _spec_from_section
from hivemind.cli.in_cell.config import build_runtime_config
from hivemind.common.errors import ConfigurationError
from hivemind.forage import ForageCapacity
from hivemind.hive import VirtualCellSpec
from hivemind.hive.backends.bootstrap import QueenEndpoint, cell_endpoint, mint_cell_bootstrap
from hivemind.manifest.env import read_in_cell_env
from hivemind.manifest.schema.placement import VirtualCellsSection
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_CLOCK = FakeClock()
_IDLE = (0.1, 0.1, 0.1)
_BUSY = (3.9, 3.9, 3.9)  # A four-core Hive Stand this loaded left the old probe no free core.


def _spec() -> VirtualCellSpec:
    """The Queen's own terminal-only Virtual Cell template from `[virtual_cells]` defaults.

    What she places a task with no Exoskeleton need by; the desktop template reserves the same.
    """
    section = VirtualCellsSection()
    return _spec_from_section(
        section, new_hive_id(_CLOCK), section.default_image, exoskeleton=False
    )


def _environ(spec: VirtualCellSpec, scratch: Path) -> dict[str, str]:
    """Mint `spec`'s bootstrap as a backend does: the environment its Cell starts with."""
    endpoint = QueenEndpoint(
        waggle_url="ws://host.docker.internal:8710",
        queen_node_id=new_node_id(_CLOCK),
        queen_verify_key_hex="ab" * 32,
    )
    bootstrap = mint_cell_bootstrap(spec.hive_id, cell_endpoint(endpoint, spec, "fake"), _CLOCK)
    return {**bootstrap.environment(), "HIVEMIND_SCRATCH_ROOT": str(scratch)}


def _reported(environ: dict[str, str]) -> ForageCapacity:
    """The capacity a Cell started with `environ` reports to the Queen."""
    return build_runtime_config(read_in_cell_env(environ), _CLOCK).spawn_config.capacity


@pytest.mark.parametrize("load", [_IDLE, _BUSY], ids=["idle", "busy"])
def test_a_virtual_cells_capacity_is_its_spec_whatever_the_hosts_load(
    load: tuple[float, float, float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The host's own load average, as a container reads it: the kernel is shared.
    monkeypatch.setattr(os, "getloadavg", lambda: load, raising=False)  # Absent on Windows.
    spec = _spec()

    capacity = _reported(_environ(spec, tmp_path))

    # Every figure placement and grants read is the spec's own, the load included; only the
    # platform facts are the Cell's probe.
    expected = spec.capacity.host.model_copy(
        update={"arch": capacity.host.arch, "os": capacity.host.os}
    )
    assert capacity.host == expected
    assert capacity.host.cpu_load == 0.0
    assert capacity.max_sub_bees == spec.capacity.max_sub_bees
    assert capacity.host.arch == (platform.machine() or "unknown")


def test_a_bootstrap_naming_no_reservation_reports_what_the_cell_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Cell minted before its bootstrap shipped a reservation: the probe is all it has.
    monkeypatch.setattr(os, "getloadavg", lambda: _BUSY, raising=False)  # Absent on Windows.
    environ = _environ(_spec(), tmp_path)
    del environ["HIVEMIND_RESERVATION"]

    capacity = _reported(environ)

    cores = capacity.host.cores
    assert capacity.host.cpu_load == pytest.approx(_BUSY[0] / cores)


def test_a_malformed_reservation_is_refused_naming_the_variable(tmp_path: Path) -> None:
    environ = {**_environ(_spec(), tmp_path), "HIVEMIND_RESERVATION": '{"cpu_cores": 0}'}

    with pytest.raises(ConfigurationError, match="HIVEMIND_RESERVATION"):
        _reported(environ)
