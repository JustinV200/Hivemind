"""Tests for hivemind.forage.models.capacity: capacity models from GpuInfo to ForageCapacity.

Fits into the Hive:
    Mirrors src/hivemind/forage/models/capacity.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.models.capacity for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity, make_footprint, make_host_capacity
from pydantic import ValidationError

from hivemind.forage.models.capacity import ForageCapacity, GpuInfo, HostCapacity, Seat
from waggle.messages.labels import OsFamily
from waggle.messages.reports import GpuReport, HostCapacityReport, PlatformReport


def test_gpu_info_rejects_more_free_vram_than_total() -> None:
    with pytest.raises(ValidationError, match="more than its total"):
        GpuInfo(name="test-gpu", vram_bytes=1, vram_free_bytes=2)


def test_gpu_info_round_trips_through_the_wire_form() -> None:
    original = GpuInfo(name="test-gpu", vram_bytes=8_000, vram_free_bytes=4_000)

    wire = original.to_wire()

    assert isinstance(wire, GpuReport)
    assert GpuInfo.from_wire(wire) == original


def test_host_capacity_rejects_more_free_memory_than_total() -> None:
    with pytest.raises(ValidationError, match="more than its total"):
        make_host_capacity(memory_bytes=1, memory_free_bytes=2)


def test_host_capacity_rejects_more_free_disk_than_total() -> None:
    with pytest.raises(ValidationError, match="more than its total"):
        make_host_capacity(disk_bytes=1, disk_free_bytes=2)


def test_host_capacity_is_frozen_and_forbids_extras() -> None:
    host = make_host_capacity()

    with pytest.raises(ValidationError, match="frozen"):
        host.cores = 1  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        HostCapacity.model_validate({**host.model_dump(), "nope": 1})


def test_host_capacity_to_wire_drops_arch_and_os() -> None:
    host = make_host_capacity(arch="aarch64", os=OsFamily.MACOS)

    wire = host.to_wire()

    assert isinstance(wire, HostCapacityReport)
    assert not hasattr(wire, "arch")
    assert not hasattr(wire, "os")


def test_host_capacity_from_wire_restores_arch_and_os_from_platform() -> None:
    host = make_host_capacity(arch="aarch64", os=OsFamily.MACOS)
    platform = PlatformReport(  # noqa: S604 -- shell is a login-shell field, not subprocess's.
        os=OsFamily.MACOS,
        distribution=None,
        architecture="aarch64",
        package_manager=None,
        shell="/bin/zsh",
        python_version=None,
    )

    restored = HostCapacity.from_wire(host.to_wire(), platform)

    assert restored == host


def test_host_capacity_round_trips_gpus_through_the_wire_form() -> None:
    host = make_host_capacity(gpus=(GpuInfo(name="gpu-0", vram_bytes=100, vram_free_bytes=50),))
    platform = PlatformReport(  # noqa: S604 -- shell is a login-shell field, not subprocess's.
        os=host.os,
        distribution=None,
        architecture=host.arch,
        package_manager=None,
        shell="/bin/bash",
        python_version=None,
    )

    restored = HostCapacity.from_wire(host.to_wire(), platform)

    assert restored == host


def test_seat_rejects_more_free_seats_than_total() -> None:
    with pytest.raises(ValidationError, match="more than its total"):
        Seat(source_id="test-source", seats_total=1, seats_free=2)


def test_seat_defaults_hosted_fields_to_none() -> None:
    seat = Seat(source_id="test-source", seats_total=4, seats_free=4)

    assert seat.requests_per_minute is None
    assert seat.tokens_per_minute is None
    assert seat.spend_cap_usd is None


def test_seat_is_frozen_and_forbids_extras() -> None:
    seat = Seat(source_id="test-source", seats_total=4, seats_free=4)

    with pytest.raises(ValidationError, match="frozen"):
        seat.seats_free = 1  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        Seat.model_validate({**seat.model_dump(), "nope": 1})


def test_role_footprint_defaults_seats_to_one_and_exoskeleton_extra_to_zero() -> None:
    footprint = make_footprint()

    assert footprint.seats == 1
    assert footprint.exoskeleton_extra_memory_bytes == 0


def test_role_footprint_has_no_role_field() -> None:
    footprint = make_footprint()

    assert not hasattr(footprint, "role")


@pytest.mark.parametrize("field", ["cpu_cores", "memory_bytes", "seats", "token_rate_per_minute"])
def test_role_footprint_rejects_a_negative_value(field: str) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        make_footprint(**{field: -1})


def test_forage_capacity_defaults_local_seats_to_empty() -> None:
    capacity = make_capacity()

    assert capacity.local_seats == ()


def test_forage_capacity_rejects_max_sub_bees_above_the_wire_bound() -> None:
    with pytest.raises(ValidationError):
        make_capacity(max_sub_bees=1_000)


def test_forage_capacity_is_frozen_and_forbids_extras() -> None:
    capacity = make_capacity()

    with pytest.raises(ValidationError, match="frozen"):
        capacity.max_sub_bees = 1  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ForageCapacity.model_validate({**capacity.model_dump(), "nope": 1})
