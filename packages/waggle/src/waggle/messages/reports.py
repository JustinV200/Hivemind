"""Define the host and capability report models that several Waggle families carry.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Cell (a
unit of compute a Worker runs in: a Virtual Cell the Hive provisions, or a Real Cell, an existing
device borrowed for a task and left exactly as found) describes itself to the Queen (the central
orchestrator) when it is probed, enrolled or measured, and the cell, swarm, forage and tool
families all carry pieces of that description. The four models here are the wire forms of that
self-description: ``PlatformReport`` (what a device runs; the one home for OS and architecture on
the wire), ``CellCapabilitiesReport`` (what a Cell can do, the flags placement and Workers branch
on instead of the Cell's kind), ``GpuReport`` (one GPU) and ``HostCapacityReport`` (Forage, that
is capacity as data: the static and live figures for one host, always a full snapshot so a report
replayed from an outbox is self-contained). They are the second half of spec section 8.1, split
out of ``waggle.messages.labels`` by responsibility so each file stays under the codingrules 5.1
size limit; ``hivemind.cell`` and ``hivemind.forage`` mirror them as ``CellCapabilities`` and
``HostCapacity``.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by the cell, swarm, forage and tool family
    modules under waggle.messages; calls into waggle.messages.base and waggle.messages.labels
    (OsFamily) only.

Key invariants:
    - No family module is imported here, so no family ever imports another through this file.
    - A free figure never exceeds its total (GpuReport, HostCapacityReport validators), so a
      placement decision can subtract without checking.
    - Every model is frozen and forbids extras through VALUE_MODEL_CONFIG, like a message, but
      none subclasses WaggleMessage, so none can ever be registered as a kind.

See Also:
    - docs/waggle/spec.md section 8.1 for the normative fields and bounds.
    - waggle.messages.labels for OsFamily and the rest of spec section 8.1.
    - waggle.messages.base for VALUE_MODEL_CONFIG.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import VALUE_MODEL_CONFIG
from waggle.messages.labels import OsFamily

MAX_DISTRIBUTION_CHARS = 64  # "Ubuntu 24.04 LTS", "Windows 11 Home": an edition name, no more.
MAX_ARCHITECTURE_CHARS = 32  # x86_64, aarch64, riscv64: a machine name from uname, no more.
MAX_PACKAGE_MANAGER_CHARS = 32  # apt, winget, brew, dnf: one word.
MAX_SHELL_CHARS = 64  # A shell path or name (/bin/bash, powershell.exe).
MAX_PYTHON_VERSION_CHARS = 32  # "3.12.14" plus a build tag at most.
MAX_NETWORK_SCOPES = 32  # Capability-syntax scopes reachable from a Cell; a handful in practice.
MAX_NETWORK_SCOPE_CHARS = 256  # One scope like net:internet or net:host:example.org.
MAX_GPU_NAME_CHARS = 128  # A vendor device string, never a description.
MAX_GPUS = 16  # More GPUs than any host the Hive will meet; bounds the report's size.
MIN_CORES = 1  # A host with no logical core cannot run a Cell.

__all__ = [
    "MAX_ARCHITECTURE_CHARS",
    "MAX_DISTRIBUTION_CHARS",
    "MAX_GPUS",
    "MAX_GPU_NAME_CHARS",
    "MAX_NETWORK_SCOPES",
    "MAX_NETWORK_SCOPE_CHARS",
    "MAX_PACKAGE_MANAGER_CHARS",
    "MAX_PYTHON_VERSION_CHARS",
    "MAX_SHELL_CHARS",
    "MIN_CORES",
    "CellCapabilitiesReport",
    "GpuReport",
    "HostCapacityReport",
    "PlatformReport",
]


class PlatformReport(BaseModel):
    """What a device or Cell runs: the one home for OS and architecture on the wire.

    Carried by cell.ready and swarm.enrol_request; tool placement reads it to pick a build.
    """

    model_config = VALUE_MODEL_CONFIG

    os: OsFamily = Field(description="The operating system family.")
    distribution: Annotated[str, Field(max_length=MAX_DISTRIBUTION_CHARS)] | None = Field(
        description="Distribution or edition name; None when not applicable."
    )
    architecture: str = Field(
        max_length=MAX_ARCHITECTURE_CHARS,
        description="CPU architecture (x86_64, aarch64).",
    )
    package_manager: Annotated[str, Field(max_length=MAX_PACKAGE_MANAGER_CHARS)] | None = Field(
        description="The package manager available, if any."
    )
    shell: str = Field(
        max_length=MAX_SHELL_CHARS,
        description="The login shell the session runs commands through.",
    )
    python_version: Annotated[str, Field(max_length=MAX_PYTHON_VERSION_CHARS)] | None = Field(
        description="The Python available to tools; None when none."
    )


class CellCapabilitiesReport(BaseModel):
    """What a Cell can do: the flags placement and Workers branch on, never the Cell's kind.

    The wire form of hivemind's CellCapabilities; carried by cell.ready and swarm.enrol_request.
    """

    model_config = VALUE_MODEL_CONFIG

    has_display: bool = Field(description="Whether a display is attached or running.")
    has_audio: bool = Field(description="Whether audio input or output is available.")
    has_browser: bool = Field(description="Whether a browser is installed.")
    can_start_display: bool = Field(
        description="Whether a virtual display (Xvfb or similar) can be started on demand."
    )
    can_host_model: bool = Field(
        description="Whether the Cell can run a local model server (a Nuc candidate)."
    )
    network_scopes: tuple[Annotated[str, Field(max_length=MAX_NETWORK_SCOPE_CHARS)], ...] = Field(
        max_length=MAX_NETWORK_SCOPES,
        description="Network scopes reachable from the Cell, in capability syntax "
        "(net:internet, ...).",
    )


class GpuReport(BaseModel):
    """One GPU on a host, as a HostCapacityReport lists it."""

    model_config = VALUE_MODEL_CONFIG

    name: str = Field(max_length=MAX_GPU_NAME_CHARS, description="The device name.")
    vram_bytes: int = Field(ge=0, description="Total VRAM.")
    vram_free_bytes: int = Field(ge=0, description="Free VRAM at report time; at most vram_bytes.")

    @model_validator(mode="after")
    def _free_within_total(self) -> GpuReport:
        """Reject more free VRAM than the device has."""
        # Placement subtracts free from total without a guard, so the report must be consistent.
        if self.vram_free_bytes > self.vram_bytes:
            raise ValueError(
                f"GPU {self.name!r} reports {self.vram_free_bytes} free VRAM bytes, more than "
                f"its total of {self.vram_bytes}."
            )
        return self


class HostCapacityReport(BaseModel):
    """Static and live figures for one host, always a full snapshot.

    The wire form of hivemind's HostCapacity, carried by forage.capacity_report; a full
    snapshot rather than a delta so a report replayed from an outbox is self-contained.
    """

    model_config = VALUE_MODEL_CONFIG

    cores: int = Field(ge=MIN_CORES, description="Logical cores.")
    memory_bytes: int = Field(description="Total memory.")
    memory_free_bytes: int = Field(description="Free memory; at most memory_bytes.")
    disk_bytes: int = Field(description="Total disk where the Hive works.")
    disk_free_bytes: int = Field(description="Free disk where the Hive works; at most disk_bytes.")
    cpu_load: float = Field(ge=0, description="One-minute load average divided by cores.")
    gpus: tuple[GpuReport, ...] = Field(max_length=MAX_GPUS, description="Every GPU on the host.")

    @model_validator(mode="after")
    def _free_within_total(self) -> HostCapacityReport:
        """Reject a free figure above its total, for memory and for disk."""
        # Same reason as GpuReport: consumers subtract without checking.
        if self.memory_free_bytes > self.memory_bytes:
            raise ValueError(
                f"The host reports {self.memory_free_bytes} free memory bytes, more than its "
                f"total of {self.memory_bytes}."
            )
        if self.disk_free_bytes > self.disk_bytes:
            raise ValueError(
                f"The host reports {self.disk_free_bytes} free disk bytes, more than its total "
                f"of {self.disk_bytes}."
            )
        return self
