"""Tests for waggle.messages.reports: the platform, capability, GPU and host capacity models.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). For each of the four report models: it
    constructs, survives the JSON round trip, rejects an extra field, and rejects the bound or
    consistency rule spec section 8.1 states for it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.reports for the module under test.
    - test_labels.py for the enums, including OsFamily, and the other value models.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from waggle.messages.labels import OsFamily
from waggle.messages.reports import (
    MAX_ARCHITECTURE_CHARS,
    MAX_GPUS,
    MAX_NETWORK_SCOPE_CHARS,
    MAX_NETWORK_SCOPES,
    CellCapabilitiesReport,
    GpuReport,
    HostCapacityReport,
    PlatformReport,
)

GIB = 1_073_741_824  # One gibibyte, so the figures below read as a real host.


def _round_trips(model: BaseModel) -> bool:
    """Whether ``model`` survives model_dump(mode="json") and model_validate unchanged."""
    return type(model).model_validate(model.model_dump(mode="json")) == model


def _platform(**overrides: object) -> PlatformReport:
    """A Linux platform report with every field set, then ``overrides``."""
    fields: dict[str, object] = {
        "os": OsFamily.LINUX,
        "distribution": "Ubuntu 24.04",
        "architecture": "x86_64",
        "package_manager": "apt",
        "shell": "/bin/bash",
        "python_version": "3.12.14",
    }
    return PlatformReport.model_validate({**fields, **overrides})


def _capabilities(**overrides: object) -> CellCapabilitiesReport:
    """A headless Cell with internet, then ``overrides``."""
    fields: dict[str, object] = {
        "has_display": False,
        "has_audio": False,
        "has_browser": True,
        "can_start_display": True,
        "can_host_model": False,
        "network_scopes": ("net:internet",),
    }
    return CellCapabilitiesReport.model_validate({**fields, **overrides})


def _gpu(**overrides: object) -> GpuReport:
    """One GPU with half its VRAM free, then ``overrides``."""
    fields: dict[str, object] = {"name": "GPU", "vram_bytes": 8 * GIB, "vram_free_bytes": 4 * GIB}
    return GpuReport.model_validate({**fields, **overrides})


def _host(**overrides: object) -> HostCapacityReport:
    """A consistent host snapshot with one GPU, then ``overrides``."""
    fields: dict[str, object] = {
        "cores": 8,
        "memory_bytes": 32 * GIB,
        "memory_free_bytes": 16 * GIB,
        "disk_bytes": 512 * GIB,
        "disk_free_bytes": 100 * GIB,
        "cpu_load": 0.25,
        "gpus": (_gpu(),),
    }
    return HostCapacityReport.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# PlatformReport
# ──────────────────────────────────────────────────────────────────────────────


def test_platform_report_constructs_and_round_trips() -> None:
    full = _platform()
    minimal = _platform(distribution=None, package_manager=None, python_version=None)

    assert full.model_dump(mode="json")["os"] == "LINUX"
    assert _round_trips(full)
    assert _round_trips(minimal)


def test_platform_report_rejects_a_long_architecture_and_an_extra_field() -> None:
    with pytest.raises(ValidationError, match=f"at most {MAX_ARCHITECTURE_CHARS}"):
        _platform(architecture="x" * (MAX_ARCHITECTURE_CHARS + 1))
    with pytest.raises(ValidationError, match="extra"):
        _platform(kernel="6.8")


# ──────────────────────────────────────────────────────────────────────────────
# CellCapabilitiesReport
# ──────────────────────────────────────────────────────────────────────────────


def test_cell_capabilities_report_constructs_and_round_trips() -> None:
    report = _capabilities()

    assert report.model_dump(mode="json")["network_scopes"] == ["net:internet"]
    assert _round_trips(report)
    assert _round_trips(_capabilities(network_scopes=()))


def test_cell_capabilities_report_bounds_the_scopes() -> None:
    with pytest.raises(ValidationError, match=f"at most {MAX_NETWORK_SCOPES}"):
        _capabilities(network_scopes=("net:internet",) * (MAX_NETWORK_SCOPES + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_NETWORK_SCOPE_CHARS}"):
        _capabilities(network_scopes=("x" * (MAX_NETWORK_SCOPE_CHARS + 1),))
    with pytest.raises(ValidationError, match="extra"):
        _capabilities(has_gpu=True)


# ──────────────────────────────────────────────────────────────────────────────
# GpuReport
# ──────────────────────────────────────────────────────────────────────────────


def test_gpu_report_constructs_and_round_trips() -> None:
    assert _round_trips(_gpu())
    assert _round_trips(_gpu(vram_bytes=0, vram_free_bytes=0))


def test_gpu_report_rejects_more_free_than_total_and_negative_figures() -> None:
    with pytest.raises(ValidationError, match="more than its total"):
        _gpu(vram_free_bytes=9 * GIB)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _gpu(vram_bytes=-1, vram_free_bytes=-1)


# ──────────────────────────────────────────────────────────────────────────────
# HostCapacityReport
# ──────────────────────────────────────────────────────────────────────────────


def test_host_capacity_report_constructs_and_round_trips() -> None:
    report = _host()

    assert report.gpus[0].name == "GPU"
    assert _round_trips(report)
    assert _round_trips(_host(gpus=()))


def test_host_capacity_report_rejects_inconsistent_figures() -> None:
    with pytest.raises(ValidationError, match="free memory bytes, more than"):
        _host(memory_free_bytes=33 * GIB)
    with pytest.raises(ValidationError, match="free disk bytes, more than"):
        _host(disk_free_bytes=513 * GIB)
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _host(cores=0)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _host(cpu_load=-0.1)
    with pytest.raises(ValidationError, match=f"at most {MAX_GPUS}"):
        _host(gpus=(_gpu(),) * (MAX_GPUS + 1))
    with pytest.raises(ValidationError, match="extra"):
        _host(hostname="stand")
