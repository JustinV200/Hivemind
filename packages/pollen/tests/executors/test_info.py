"""Test what the packet knows about its device: the probes behind enrolment and capacity reports.

The Pollen Packet (the thin gateway program on a device the Hive borrows) tells the Queen (the
Hive's central orchestrator) about the device it runs on: a platform report, the capability flags
and the host capacity at enrolment, and a full capacity snapshot after each reconnection, when a
figure moves materially and on a timer (gateway-contract section 8). A gateway hosts no models and
runs no bees, so its reports say so. Everything it cannot read is zero, never a guess, and none of
the text in a report is trusted by the Hive. These tests run the real probes on the test host and
check the shapes and the rule for what counts as a material change.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.info.

Key invariants:
    - A report fits the wire models, whatever the host.
    - Free memory or disk must move by a fifth of the total, or a total must change, to be
      worth a report.

See Also:
    - docs/waggle/gateway-contract.md section 8 and docs/waggle/enrolment.md section 4.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pollen.executors.info import (
    DEFAULT_MAX_SUB_BEES,
    build_capacity_report,
    capabilities_report,
    capacity_changed,
    host_capacity,
    hostname,
    platform_report,
    runtime_version,
)
from pollen.platform import select_shim
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.forage import CapacityTrigger
from waggle.messages.reports import HostCapacityReport

CLOCK = FakeClock()


def _host(**changes: object) -> HostCapacityReport:
    """A host snapshot with round figures, any of which a test may change."""
    values: dict[str, object] = {
        "cores": 4,
        "memory_bytes": 1000,
        "memory_free_bytes": 500,
        "disk_bytes": 10_000,
        "disk_free_bytes": 5_000,
        "cpu_load": 0.2,
        "gpus": (),
    }
    values.update(changes)
    return HostCapacityReport(**values)


def test_the_version_and_hostname_fit_the_wire_bounds() -> None:
    assert 0 < len(runtime_version()) <= 32
    assert 0 < len(hostname()) <= 253


def test_the_version_falls_back_when_the_package_metadata_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata

    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr("pollen.executors.info.metadata.version", missing)
    assert runtime_version() == "0.1.0.dev0"


def test_the_platform_and_capability_reports_describe_this_host() -> None:
    shim = select_shim()
    platform = platform_report(shim)
    assert platform.os.value in {"LINUX", "MACOS", "WINDOWS"} and platform.architecture
    capabilities = capabilities_report(shim)
    assert (capabilities.can_host_model, capabilities.real_display_allowed) == (False, False)
    assert capabilities.network_scopes == ()


def test_a_single_file_build_does_not_claim_a_python_for_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert platform_report(select_shim()).python_version is None


def test_the_capacity_of_a_real_directory_is_measured(tmp_path: Path) -> None:
    host = host_capacity(tmp_path)
    assert host.cores >= 1 and host.memory_bytes > 0 and host.disk_bytes > 0
    assert host.memory_free_bytes <= host.memory_bytes and host.disk_free_bytes <= host.disk_bytes
    assert host.gpus == ()


def test_the_capacity_of_a_directory_that_does_not_exist_yet_is_its_nearest_parents(
    tmp_path: Path,
) -> None:
    assert (
        host_capacity(tmp_path / "not" / "yet" / "made").disk_bytes
        == host_capacity(tmp_path).disk_bytes
    )


def test_a_disk_that_cannot_be_measured_reports_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: str) -> object:
        raise OSError("no such device")

    monkeypatch.setattr("pollen.executors.info.psutil.disk_usage", broken)
    host = host_capacity(tmp_path)
    assert (host.disk_bytes, host.disk_free_bytes) == (0, 0)


@pytest.mark.parametrize("cores", [1, 2, 4, 64])
def test_the_report_hosts_no_models_and_runs_no_bees(cores: int) -> None:
    report = build_capacity_report(new_cell_id(CLOCK), CapacityTrigger.CHANGED, _host(cores=cores))
    assert report.model_servers == () and report.trigger is CapacityTrigger.CHANGED
    assert report.max_sub_bees == max(1, min(cores, DEFAULT_MAX_SUB_BEES))
    assert report.usage.sub_bees_active == 0 and report.usage.seats_exported == 0


@pytest.mark.parametrize(
    ("changes", "material"),
    [
        ({}, False),
        ({"cpu_load": 0.9}, False),  # Load is volatile and never counts alone.
        ({"memory_free_bytes": 400}, False),
        ({"memory_free_bytes": 300}, True),  # A fifth of the total.
        ({"disk_free_bytes": 3_000}, True),
        ({"disk_free_bytes": 4_900}, False),
        ({"cores": 8}, True),
        ({"memory_bytes": 2000}, True),
        ({"disk_bytes": 20_000}, True),
    ],
)
def test_only_a_material_change_is_worth_a_report(
    changes: dict[str, object], material: bool
) -> None:
    assert capacity_changed(_host(), _host(**changes)) is material
