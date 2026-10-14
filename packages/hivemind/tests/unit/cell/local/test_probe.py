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
import shutil
import sys
from pathlib import Path

import pytest
from builders.cells import make_hive_stand_config

from hivemind.cell.errors import ProbeError
from hivemind.cell.local.probe import (
    AUDIO_PROGRAMS,
    WINDOW_MANAGERS,
    X11_DISPLAY_PROGRAMS,
    _memory_bytes,
    probe_host,
    refresh_live,
)


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


def _only_on_path(monkeypatch: pytest.MonkeyPatch, programs: set[str]) -> None:
    """Make `shutil.which` find exactly `programs`, so the probe sees a controlled PATH."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *args, **kwargs: f"/usr/bin/{name}" if name in programs else None,
    )


_X11 = {*X11_DISPLAY_PROGRAMS, WINDOW_MANAGERS[0]}


@pytest.mark.skipif(sys.platform != "linux", reason="the X11 display is Linux-only")
def test_probe_host_can_start_a_display_only_with_every_x11_program_and_a_window_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_hive_stand_config(tmp_path)

    _only_on_path(monkeypatch, _X11)
    assert probe_host(config).capabilities.can_start_display is True

    # Any one program missing, or no window manager at all (xdotool's pointer moves are ignored
    # by a bare Xvfb), and the Hive cannot start a usable display.
    for missing in _X11:
        _only_on_path(monkeypatch, _X11 - {missing})
        assert probe_host(config).capabilities.can_start_display is False, missing


@pytest.mark.skipif(sys.platform != "linux", reason="the PulseAudio server is Linux-only here")
def test_probe_host_has_audio_only_with_every_sound_server_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_hive_stand_config(tmp_path)

    _only_on_path(monkeypatch, set(AUDIO_PROGRAMS))
    assert probe_host(config).capabilities.has_audio is True
    _only_on_path(monkeypatch, set(AUDIO_PROGRAMS) - {"parec"})
    assert probe_host(config).capabilities.has_audio is False


def test_probe_host_real_display_allowed_is_the_operators_opt_in_alone(tmp_path: Path) -> None:
    denied = make_hive_stand_config(tmp_path)
    allowed = denied.model_copy(update={"real_display_allowed": True})

    # Never inferred from a display being present: only the manifest's own opt-in sets it.
    assert probe_host(denied).capabilities.real_display_allowed is False
    assert probe_host(allowed).capabilities.real_display_allowed is True


def test_probe_host_model_hosting_is_false_unless_the_operator_declared_a_server(
    tmp_path: Path,
) -> None:
    # Roadmap step 8.3: a probe cannot know whether a server program is installed, so the
    # capability follows HiveStandConfig.can_host_model, which the composition root sets from
    # [llm.servers].
    declined = make_hive_stand_config(tmp_path)
    declared = declined.model_copy(update={"can_host_model": True})

    assert probe_host(declined).capabilities.can_host_model is False
    assert probe_host(declared).capabilities.can_host_model is True


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


_MEMINFO = Path("/proc/meminfo")
_MEMORY_DRIFT_BYTES = 64 * 1024 * 1024  # The two reads are a moment apart; memory moves meanwhile.


@pytest.mark.skipif(not _MEMINFO.exists(), reason="Linux's /proc/meminfo only")
def test_free_memory_is_what_the_kernel_can_give_not_what_it_has_left_idle() -> None:
    # MemFree leaves out the page cache the kernel hands back on demand, so a warm host read as
    # out of memory and every grant on it waited; the probe reports MemAvailable instead.
    fields = dict(line.split(":", 1) for line in _MEMINFO.read_text().splitlines() if ":" in line)
    available_bytes = int(fields["MemAvailable"].split()[0]) * 1024

    _total, free = _memory_bytes()

    assert abs(free - available_bytes) < _MEMORY_DRIFT_BYTES
