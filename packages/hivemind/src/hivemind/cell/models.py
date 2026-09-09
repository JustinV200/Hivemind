"""Define Cell, CellKind and CellCapabilities: the Hive's one description of a machine to run on.

A Cell (a unit of compute a Worker runs in or on) is either Virtual (a VM or container the Hive
provisions and later destroys) or Real (an existing device: the Hive Stand or a Swarm device,
borrowed for a task and returned unchanged) -- `CellKind`. `CellCapabilities` is the one place
platform facts (what the machine is: its OS family, architecture, distribution, shell, package
manager, Python) and capability flags (what it can do: has_display, has_audio, has_browser,
can_start_display, can_host_model, network_scopes) live together, so placement and Worker tool
code branch on what a Cell can actually do rather than on its kind (codingrules section 8.7,
"Branch on capabilities, never on kind"). `Cell` ties a `CellId`, its `kind`, `capabilities`,
`capacity` (`hivemind.forage.ForageCapacity`, capacity as data), `access_level` and `comb_shield`
together as the one record every layer above `cell` reads to decide anything about where a task
runs.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by queen.placement (Layer 6, which
    picks a Cell), wardens (Layer 5, which each own one Cell), workers (Layer 4, which read
    capabilities, never kind) and every RealCellSource (`hive`, `swarm`, `cell.local`) that builds
    one. Calls into hivemind.cell.needs (OsFamily), hivemind.cell.tiers (AccessLevel,
    CombShieldLevel), hivemind.forage (ForageCapacity) and waggle.messages.reports (the wire split
    this module's to_wire/from_wire convert through) only.

Key invariants:
    - A REAL Cell is never CombShieldLevel.NIGHT_VEIL (codingrules section 8.7: "Real Cell
      constraint: Real Cells can run Meadow or Propolis, but never Night Veil").
    - A VIRTUAL Cell is always AccessLevel.FULL (codingrules section 6.1: "Virtual Cells are
      always FULL").
    - CellCapabilities.to_wire/from_wire split platform facts onto waggle's PlatformReport and
      capability flags onto CellCapabilitiesReport, the same split hivemind.forage.HostCapacity
      uses for the same reason (the wire carries the two on separate messages).

See Also:
    - .claude/codingrules.md section 8.7 for "One abstraction, two sources" and the Real/Virtual
      constraints this module's Cell validator enforces.
    - .claude/codingrules.md section 6.1 for the Cell / CellKind / CellCapabilities row.
    - README.md "Core concept 4: Real vs Virtual Cells" for the plain-language version.
    - hivemind.cell.session for CellSession, the terminal every Cell offers.
    - hivemind.forage for ForageCapacity, the capacity-as-data half of a Cell's report.
    - waggle.messages.reports for PlatformReport and CellCapabilitiesReport, the wire forms this
      module's CellCapabilities converts to and from.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.cell.needs import OsFamily
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.forage import ForageCapacity
from waggle.messages import OsFamily as WireOsFamily
from waggle.messages.base import CellIdField
from waggle.messages.reports import (
    MAX_ARCHITECTURE_CHARS,
    MAX_DISTRIBUTION_CHARS,
    MAX_NETWORK_SCOPES,
    MAX_PACKAGE_MANAGER_CHARS,
    MAX_PYTHON_VERSION_CHARS,
    MAX_SHELL_CHARS,
    CellCapabilitiesReport,
    PlatformReport,
)

MAX_CELL_NAME_CHARS = 128  # A human-readable label ("hive-stand", "pixel-7a"), never a sentence.
MAX_SOURCE_NAME_CHARS = 64  # The RealCellSource/backend name ("hive_stand", "swarm", "hive").

# codingrules 8.5: frozen, extra-forbidding config every model in this module shares.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "MAX_CELL_NAME_CHARS",
    "MAX_SOURCE_NAME_CHARS",
    "Cell",
    "CellCapabilities",
    "CellKind",
]


class CellKind(Enum):
    """Whether a Cell is provisioned (Virtual) or borrowed (Real).

    codingrules section 8.7: "Branch on capabilities, never on kind." CellKind matters to exactly
    two callers outside this package: queen.placement (choose) and workers.roles.undertaker
    (destroy versus release); `scripts/check_no_kind_branches.py` enforces that everywhere else.
    """

    REAL = "REAL"  # An existing device (the Hive Stand or a Swarm node), leased and released.
    VIRTUAL = "VIRTUAL"  # A VM or container the Hive provisions and later destroys or overwinters.


class CellCapabilities(BaseModel):
    """What a Cell is and what it can do: platform facts plus capability flags, together.

    Platform facts (`os`, `arch`, `distribution`, `shell`, `package_manager`, `python_version`)
    answer "what does this machine run"; capability flags (`has_display` through
    `network_scopes`) answer "what can the Hive do with it". Kept on one model, rather than split
    the way their wire forms are, because every caller above `cell` reasons about a Cell as one
    thing to place work on.
    """

    model_config = _MODEL_CONFIG

    os: OsFamily = Field(description="The operating system family.")
    arch: str = Field(
        max_length=MAX_ARCHITECTURE_CHARS, description="CPU architecture (x86_64, aarch64)."
    )
    distribution: str | None = Field(
        max_length=MAX_DISTRIBUTION_CHARS, description="Distribution or edition name, if any."
    )
    shell: str = Field(
        max_length=MAX_SHELL_CHARS, description="The login shell sessions run commands through."
    )
    package_manager: str | None = Field(
        max_length=MAX_PACKAGE_MANAGER_CHARS, description="The package manager available, if any."
    )
    python_version: str | None = Field(
        max_length=MAX_PYTHON_VERSION_CHARS, description="The Python available to tools, if any."
    )
    has_display: bool = Field(description="Whether a display is attached or running.")
    has_audio: bool = Field(description="Whether audio input or output is available.")
    has_browser: bool = Field(description="Whether a browser is installed.")
    can_start_display: bool = Field(
        description="Whether a virtual display (Xvfb or similar) can be started on demand."
    )
    can_host_model: bool = Field(
        description="Whether the Cell can run a local model server (a Nuc candidate)."
    )
    network_scopes: tuple[str, ...] = Field(
        max_length=MAX_NETWORK_SCOPES,
        description="Network scopes reachable from the Cell, in capability syntax.",
    )

    @classmethod
    def from_wire(
        cls, platform: PlatformReport, report: CellCapabilitiesReport
    ) -> CellCapabilities:
        """Build a CellCapabilities from the two wire reports that together describe a Cell.

        Args:
            platform: The platform facts (`cell.ready` or `swarm.enrol_request` carries this).
            report: The capability flags, from the same message.

        Returns:
            The equivalent CellCapabilities.
        """
        return cls(
            os=OsFamily(platform.os.value),
            arch=platform.architecture,
            distribution=platform.distribution,
            shell=platform.shell,
            package_manager=platform.package_manager,
            python_version=platform.python_version,
            has_display=report.has_display,
            has_audio=report.has_audio,
            has_browser=report.has_browser,
            can_start_display=report.can_start_display,
            can_host_model=report.can_host_model,
            network_scopes=report.network_scopes,
        )

    def to_wire(self) -> tuple[PlatformReport, CellCapabilitiesReport]:
        """Build the two wire reports this CellCapabilities corresponds to.

        Returns:
            A `(PlatformReport, CellCapabilitiesReport)` pair carrying this model's fields split
            the way the wire carries them.
        """
        platform = PlatformReport(
            os=WireOsFamily(self.os.value),
            distribution=self.distribution,
            architecture=self.arch,
            package_manager=self.package_manager,
            shell=self.shell,
            python_version=self.python_version,
        )
        report = CellCapabilitiesReport(
            has_display=self.has_display,
            has_audio=self.has_audio,
            has_browser=self.has_browser,
            can_start_display=self.can_start_display,
            can_host_model=self.can_host_model,
            network_scopes=self.network_scopes,
        )
        return platform, report


class Cell(BaseModel):
    """One machine a Worker can run in or on: Real or Virtual, with its capabilities and tiers.

    The one record queen.placement, a Warden and every Worker tool reads to know where a task is
    running and what that Cell allows; never mutated in place, since state changes (a lease
    opening, a Cell's capacity changing) produce a new Cell via `model_copy`.
    """

    model_config = _MODEL_CONFIG

    id: CellIdField = Field(description="This Cell's own id.")
    kind: CellKind = Field(description="REAL (borrowed) or VIRTUAL (provisioned).")
    name: str = Field(
        max_length=MAX_CELL_NAME_CHARS, description="A human-readable label ('hive-stand')."
    )
    source: str = Field(
        max_length=MAX_SOURCE_NAME_CHARS,
        description="The RealCellSource or backend name that produced this Cell "
        "('hive_stand', 'swarm', 'hive').",
    )
    capabilities: CellCapabilities = Field(description="What this Cell is and can do.")
    capacity: ForageCapacity = Field(description="This Cell's Forage report: host, seats, cap.")
    access_level: AccessLevel = Field(
        description="How much of this Cell the Hive may touch; always FULL for a Virtual Cell."
    )
    comb_shield: CombShieldLevel = Field(
        description="This Cell's security tier; never NIGHT_VEIL for a Real Cell."
    )

    @model_validator(mode="after")
    def _kind_constrains_tier_and_access(self) -> Cell:
        """Reject a REAL Cell at NIGHT_VEIL and a VIRTUAL Cell below FULL access.

        Returns:
            This Cell unchanged, once both constraints hold.

        Raises:
            ValueError: `kind` is REAL and `comb_shield` is NIGHT_VEIL, or `kind` is VIRTUAL and
                `access_level` is not FULL.
        """
        # codingrules 8.7: "Real Cells can run Meadow or Propolis, but never Night Veil" -- Night
        # Veil's attestation (VPN, Tor, teardown-only lifecycle) only exists for a Virtual image.
        if self.kind is CellKind.REAL and self.comb_shield is CombShieldLevel.NIGHT_VEIL:
            raise ValueError(
                f"Cell {self.id!r} is REAL but comb_shield is NIGHT_VEIL: Real Cells are never "
                "Night Veil (codingrules section 8.7)."
            )
        # codingrules 6.1: "Virtual Cells are always FULL" -- a Virtual Cell is created for this
        # task alone, so there is no borrowed owner's access to cap it below FULL.
        if self.kind is CellKind.VIRTUAL and self.access_level is not AccessLevel.FULL:
            raise ValueError(
                f"Cell {self.id!r} is VIRTUAL but access_level is {self.access_level.name}: "
                "Virtual Cells are always FULL access (codingrules section 6.1)."
            )
        return self
