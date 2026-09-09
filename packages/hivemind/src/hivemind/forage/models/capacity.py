"""Define GpuInfo, HostCapacity, Seat, RoleFootprint and ForageCapacity: capacity, not one number.

Forage (the Hive's capacity, modelled as data) is several dimensions and never one number
(codingrules section 8.10). `HostCapacity` is host compute for one Cell (a unit of compute a
Worker runs in): static figures (cores, memory, disk, GPUs and their VRAM, architecture and
operating system) plus live figures (load, free memory, free VRAM) that change on every heartbeat.
`Seat` is the other dimension: one concurrent request on one model on one server, or for a hosted
provider the equivalent -- requests and tokens per minute plus a spend cap. `RoleFootprint` is what
one bee of a role costs its Cell while it runs: cpu, memory, one seat while mid-call, an estimated
token rate, and extra memory an Exoskeleton (the GUI-driving toolkit) adds. It carries no role
field of its own; the Hive Manifest's `[forage.roles.<role>]` table keys it by the lowercase
`waggle.messages.task.WorkerRole` member name, the same convention every per-role table in the Hive
follows. `ForageCapacity` is what one Cell reports: its `HostCapacity`, the `Seat`s any model
server running on it offers, and its own cap on concurrent sub-bees.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by `hivemind.forage.allocate`
    (a Cell's `ForageCapacity` and a role's `RoleFootprint` are two of `GrantInputs`' fields) and
    embedded by `hivemind.manifest` for `[forage.roles.<role>]`. Calls into `waggle.messages.base`,
    `waggle.messages.labels` and `waggle.messages.reports` only, for the wire forms `HostCapacity`
    converts to and from.

Key invariants:
    - GpuInfo.vram_free_bytes and HostCapacity's *_free_bytes fields never exceed their matching
      totals, exactly like the wire report validators they mirror, so the allocator can subtract
      without checking.
    - HostCapacity.to_wire drops arch and os (the wire HostCapacityReport carries neither; they
      travel on PlatformReport instead), and HostCapacity.from_wire takes a PlatformReport to
      supply them back, the same split `hivemind.cell.CellCapabilities` uses for the same reason.
    - RoleFootprint.seats defaults to 1: "one seat while mid-call" (roadmap step 3.12) describes
      the common case, not a variable the manifest usually overrides.

See Also:
    - .claude/roadmap.md step 3.12 for the field-by-field description this module implements.
    - .claude/codingrules.md section 8.10 for Forage's dimensions and why footprints gate spawning.
    - waggle.messages.reports for HostCapacityReport, GpuReport and PlatformReport.
    - hivemind.forage.models.sources for ModelSource, the Forage map entry a Seat's source_id names.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.messages.base import MAX_SUB_BEES_ON_WIRE
from waggle.messages.forage.values import MAX_SOURCE_ID_CHARS
from waggle.messages.labels import OsFamily
from waggle.messages.reports import (
    MAX_GPU_NAME_CHARS,
    MAX_GPUS,
    MIN_CORES,
    GpuReport,
    HostCapacityReport,
    PlatformReport,
)

DEFAULT_EXOSKELETON_EXTRA_MEMORY_BYTES = 0  # Most roles never attach an Exoskeleton.

__all__ = [
    "DEFAULT_EXOSKELETON_EXTRA_MEMORY_BYTES",
    "ForageCapacity",
    "GpuInfo",
    "HostCapacity",
    "RoleFootprint",
    "Seat",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class GpuInfo(BaseModel):
    """One GPU on a host, with total and free VRAM; mirrors the wire `GpuReport`."""

    model_config = _MODEL_CONFIG

    name: str = Field(max_length=MAX_GPU_NAME_CHARS, description="The device name.")
    vram_bytes: Annotated[int, Field(ge=0)] = Field(description="Total VRAM.")
    vram_free_bytes: Annotated[int, Field(ge=0)] = Field(
        description="Free VRAM right now; at most vram_bytes."
    )

    @model_validator(mode="after")
    def _free_within_total(self) -> GpuInfo:
        """Reject more free VRAM than the device has, matching the wire report's own rule."""
        # The allocator subtracts free from total without a guard, so this must hold before it
        # ever reaches allocate.grant.
        if self.vram_free_bytes > self.vram_bytes:
            raise ValueError(
                f"GPU {self.name!r} reports {self.vram_free_bytes} free VRAM bytes, more than "
                f"its total of {self.vram_bytes}."
            )
        return self

    @classmethod
    def from_wire(cls, wire: GpuReport) -> GpuInfo:
        """Build a GpuInfo from the wire `GpuReport` a `forage.capacity_report` carries."""
        return cls(name=wire.name, vram_bytes=wire.vram_bytes, vram_free_bytes=wire.vram_free_bytes)

    def to_wire(self) -> GpuReport:
        """Build the wire `GpuReport` this GpuInfo corresponds to."""
        return GpuReport(
            name=self.name, vram_bytes=self.vram_bytes, vram_free_bytes=self.vram_free_bytes
        )


class HostCapacity(BaseModel):
    """Static and live compute figures for one host: cores, memory, disk, GPUs, arch and OS.

    The wire `HostCapacityReport` carries every field except `arch` and `os` (those travel on
    `PlatformReport` instead, `cell/session.py`'s own split for the same reason), so `to_wire`
    drops them and `from_wire` takes a `PlatformReport` to restore them.
    """

    model_config = _MODEL_CONFIG

    cores: Annotated[int, Field(ge=MIN_CORES)] = Field(description="Logical cores.")
    memory_bytes: Annotated[int, Field(ge=0)] = Field(description="Total memory.")
    memory_free_bytes: Annotated[int, Field(ge=0)] = Field(
        description="Free memory right now; at most memory_bytes."
    )
    disk_bytes: Annotated[int, Field(ge=0)] = Field(description="Total disk where the Hive works.")
    disk_free_bytes: Annotated[int, Field(ge=0)] = Field(
        description="Free disk right now; at most disk_bytes."
    )
    cpu_load: Annotated[float, Field(ge=0)] = Field(
        description="One-minute load average divided by cores."
    )
    gpus: tuple[GpuInfo, ...] = Field(
        default=(), max_length=MAX_GPUS, description="Every GPU on the host."
    )
    arch: str = Field(description="CPU architecture (x86_64, aarch64).")
    os: OsFamily = Field(description="The operating system family.")

    @model_validator(mode="after")
    def _free_within_total(self) -> HostCapacity:
        """Reject a free figure above its total, for memory and for disk."""
        # Same reason as GpuInfo: the allocator subtracts without checking.
        if self.memory_free_bytes > self.memory_bytes:
            raise ValueError(
                f"Host reports {self.memory_free_bytes} free memory bytes, more than its total "
                f"of {self.memory_bytes}."
            )
        if self.disk_free_bytes > self.disk_bytes:
            raise ValueError(
                f"Host reports {self.disk_free_bytes} free disk bytes, more than its total of "
                f"{self.disk_bytes}."
            )
        return self

    @classmethod
    def from_wire(cls, wire: HostCapacityReport, platform: PlatformReport) -> HostCapacity:
        """Build a HostCapacity from the wire report plus the platform figures it lacks.

        Args:
            wire: The static and live compute figures from a `forage.capacity_report`.
            platform: The `PlatformReport` carrying `arch` and `os`, normally read off the same
                Cell's `cell.ready` message.

        Returns:
            The equivalent HostCapacity.
        """
        return cls(
            cores=wire.cores,
            memory_bytes=wire.memory_bytes,
            memory_free_bytes=wire.memory_free_bytes,
            disk_bytes=wire.disk_bytes,
            disk_free_bytes=wire.disk_free_bytes,
            cpu_load=wire.cpu_load,
            gpus=tuple(GpuInfo.from_wire(gpu) for gpu in wire.gpus),
            arch=platform.architecture,
            os=platform.os,
        )

    def to_wire(self) -> HostCapacityReport:
        """Build the wire HostCapacityReport; arch and os are dropped (see the class docstring)."""
        return HostCapacityReport(
            cores=self.cores,
            memory_bytes=self.memory_bytes,
            memory_free_bytes=self.memory_free_bytes,
            disk_bytes=self.disk_bytes,
            disk_free_bytes=self.disk_free_bytes,
            cpu_load=self.cpu_load,
            gpus=tuple(gpu.to_wire() for gpu in self.gpus),
        )


class Seat(BaseModel):
    """Concurrent-request capacity on one source: total and free seats, or a hosted rate limit.

    "One concurrent request on one model on one server; for hosted providers the equivalent is
    requests and tokens per minute plus spend caps" (roadmap step 3.12).
    """

    model_config = _MODEL_CONFIG

    source_id: Annotated[str, Field(max_length=MAX_SOURCE_ID_CHARS)] = Field(
        description="The Forage map source (hivemind.forage.models.sources.ModelSource) this "
        "seat capacity is on."
    )
    seats_total: Annotated[int, Field(ge=0)] = Field(
        description="Concurrent requests the source allows."
    )
    seats_free: Annotated[int, Field(ge=0)] = Field(
        description="Concurrent requests currently free; at most seats_total."
    )
    requests_per_minute: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="Requests per minute this source allows, for a hosted provider; None when "
        "not metered that way.",
    )
    tokens_per_minute: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="Tokens per minute this source allows, for a hosted provider; None when not "
        "metered that way.",
    )
    spend_cap_usd: Annotated[float, Field(ge=0)] | None = Field(
        default=None,
        description="A per-period spend cap on this source, for a hosted provider; None when "
        "unbounded or not applicable.",
    )

    @model_validator(mode="after")
    def _free_within_total(self) -> Seat:
        """Reject more free seats than the source allows."""
        if self.seats_free > self.seats_total:
            raise ValueError(
                f"Source {self.source_id!r} reports {self.seats_free} free seats, more than its "
                f"total of {self.seats_total}."
            )
        return self


class RoleFootprint(BaseModel):
    """What one bee of a role costs its Cell while it runs; keyed by role in the manifest, not here.

    "`RoleFootprint` has no role field; the manifest keys it" -- the Hive Manifest's
    `[forage.roles.<role>]` table names the role, so this model only ever describes the cost.
    """

    model_config = _MODEL_CONFIG

    cpu_cores: Annotated[float, Field(ge=0)] = Field(
        description="Logical cores this role's bee occupies while running."
    )
    memory_bytes: Annotated[int, Field(ge=0)] = Field(
        description="Memory this role's bee occupies while running."
    )
    seats: Annotated[int, Field(ge=0)] = Field(
        default=1, description="Model seats held while mid-call; one for almost every role."
    )
    token_rate_per_minute: Annotated[float, Field(ge=0)] = Field(
        description="Estimated tokens per minute this role's bee consumes while active."
    )
    exoskeleton_extra_memory_bytes: Annotated[int, Field(ge=0)] = Field(
        default=DEFAULT_EXOSKELETON_EXTRA_MEMORY_BYTES,
        description="Extra memory this role needs when it attaches an Exoskeleton (the "
        "GUI-driving toolkit); 0 for a role that never does.",
    )


class ForageCapacity(BaseModel):
    """One Cell's Forage report: its HostCapacity, its Seats, and its own sub-bee cap."""

    model_config = _MODEL_CONFIG

    host: HostCapacity = Field(description="The Cell's host compute figures.")
    local_seats: tuple[Seat, ...] = Field(
        default=(), description="Seat capacity on every model server running on this Cell."
    )
    max_sub_bees: Annotated[int, Field(ge=0, le=MAX_SUB_BEES_ON_WIRE)] = Field(
        description="The Cell's own cap on concurrent sub-bees, independent of what capacity "
        "would otherwise allow (access-level and left-as-found reasons, codingrules 8.10)."
    )
