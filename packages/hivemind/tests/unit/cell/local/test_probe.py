"""Unit tests for hivemind.cell.local.probe: probe_host and refresh_live.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/probe.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.probe for the module under test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from builders.cells import make_hive_stand_config

from hivemind.cell.errors import ProbeError
from hivemind.cell.local.probe import probe_host, refresh_live


def test_probe_host_raises_probe_error_when_no_cores_can_be_determined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    config = make_hive_stand_config(tmp_path)

    with pytest.raises(ProbeError):
        probe_host(config)


def test_probe_host_never_raises_when_config_overrides_cores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    config = make_hive_stand_config(tmp_path, cores=4)

    result = probe_host(config)

    assert result.capacity.host.cores == 4


def test_probe_host_config_overrides_win_over_the_probed_figures(tmp_path: Path) -> None:
    config = make_hive_stand_config(tmp_path, cores=2, memory_bytes=1024, max_sub_bees=1)

    result = probe_host(config)

    assert result.capacity.host.cores == 2
    assert result.capacity.host.memory_bytes == 1024
    assert result.capacity.max_sub_bees == 1


def test_probe_host_exoskeleton_and_model_hosting_capabilities_are_false_in_v0(
    tmp_path: Path,
) -> None:
    config = make_hive_stand_config(tmp_path)

    result = probe_host(config)

    assert result.capabilities.can_start_display is False
    assert result.capabilities.can_host_model is False


def test_refresh_live_keeps_static_totals_and_only_recomputes_live_figures(
    tmp_path: Path,
) -> None:
    config = make_hive_stand_config(tmp_path, cores=4, memory_bytes=1_000_000)
    static = probe_host(config).capacity

    refreshed = refresh_live(config, static)

    assert refreshed.host.cores == static.host.cores
    assert refreshed.host.memory_bytes == static.host.memory_bytes
    assert refreshed.max_sub_bees == static.max_sub_bees
    assert refreshed.host.memory_free_bytes <= refreshed.host.memory_bytes
