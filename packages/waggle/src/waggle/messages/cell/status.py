"""Define the cell family's own-life messages and values: a Cell ready, its heartbeat, its needs.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Cell is
a unit of compute: a Virtual Cell the Hive provisions, or a Real Cell, an existing device borrowed
for a task and left exactly as found. The cell family is the life of a Cell and of the leases on
it, and this module is the Cell's own half: ``CellReady`` announces a Cell as booted, probed and,
where its tier requires it, attested, so placement (the Queen's, the central orchestrator's,
choice of a Cell for a task) can consider it; ``CellHeartbeat`` is the Cell's liveness, distinct
from the bee heartbeat, carrying its mode, leases and shield state but never capacity, which
travels only on forage.capacity_report. The value models are ``AttestationCheck`` (one readiness
check) and ``TaskNeedsReport`` (what placement decides on, the wire form of ``TaskNeeds``), and
the enums are the closed sets the family's non-wax messages carry. The tenancy lifecycle
(cell.request, cell.teardown_request, cell.lease_opened, cell.lease_released) lives in
``waggle.messages.cell.leases`` and the Cell Wax notes in ``waggle.messages.cell.wax``, split
out by responsibility so every file stays under the codingrules 5.1 size limit. Every bound is
a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Warden (the always-on supervisor of one Cell) and read by
    the Queen; imported by waggle.messages.cell.leases for the enums and TaskNeedsReport; calls
    into waggle.messages.base, waggle.messages.labels and waggle.messages.reports only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config; every value
      model does the same through VALUE_MODEL_CONFIG without being a WaggleMessage.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A CellReady carries attestation results exactly when its tier attests (any tier but
      MEADOW), so a tiered Cell can never report ready with no checks run.

See Also:
    - docs/waggle/spec.md section 8.5 for the normative fields, bounds and validators.
    - waggle.messages.cell.leases for the tenancy lifecycle and waggle.messages.cell.wax for
      the Cell Wax notes.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import (
    VALUE_MODEL_CONFIG,
    CellIdField,
    LeaseIdField,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.labels import AccessLevel, CombShieldLevel, OsFamily, Tempo
from waggle.messages.reports import (
    MAX_NETWORK_SCOPE_CHARS,
    MAX_NETWORK_SCOPES,
    CellCapabilitiesReport,
    PlatformReport,
)

MAX_CHECK_NAME_CHARS = 64  # An attestation check's name is an identifier, never a sentence.
MAX_CHECK_DETAIL_CHARS = 1_000  # What the check saw: a sentence or two, the size of a reason.
MAX_ATTESTATION_CHECKS = 32  # A tier's attestation runs a handful of checks; room for growth.
MAX_RUNTIME_VERSION_CHARS = 64  # A PEP 440 version plus a git tag at most.
MAX_LEASE_IDS = 64  # Open leases on one Cell; a Real Cell has one, a Virtual Cell a few.

__all__ = [
    "MAX_ATTESTATION_CHECKS",
    "MAX_CHECK_DETAIL_CHARS",
    "MAX_CHECK_NAME_CHARS",
    "MAX_LEASE_IDS",
    "MAX_RUNTIME_VERSION_CHARS",
    "AttestationCheck",
    "CellHeartbeat",
    "CellMode",
    "CellReady",
    "IsolationNeed",
    "ReleaseCause",
    "TaskNeedsReport",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class CellMode(Enum):
    """A Cell's current mode, as its heartbeat reports it."""

    ACTIVE = "ACTIVE"  # Bees running.
    IDLE = "IDLE"  # A Virtual Cell eligible for Overwintering, the dormant pool.
    WATCH = "WATCH"  # A Real Cell's Warden observing read-only.


class IsolationNeed(Enum):
    """How much a task needs its own Cell rather than a shared one."""

    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    NONE = "NONE"


class ReleaseCause(Enum):
    """Why a tenancy ends: the rule that asked for or performed the release."""

    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    STING_CUT = "STING_CUT"  # The human's per-Cell emergency disconnect.
    DEAD_MAN = "DEAD_MAN"  # A device with no Warden on it lost its link too long.
    ORPHAN_SWEEP = "ORPHAN_SWEEP"  # Reconciliation found a lease no holder claims.
    HOLDER_LOST = "HOLDER_LOST"  # The holding Warden stopped answering.


# ──────────────────────────────────────────────────────────────────────────────
# Value models
# ──────────────────────────────────────────────────────────────────────────────


class AttestationCheck(BaseModel):
    """One readiness check a tier's attestation ran, pass or fail."""

    model_config = VALUE_MODEL_CONFIG

    name: str = Field(max_length=MAX_CHECK_NAME_CHARS, description="The check's name.")
    has_passed: bool = Field(description="Whether the check passed.")
    detail: str = Field(
        max_length=MAX_CHECK_DETAIL_CHARS, description="What the check saw, pass or fail."
    )


class TaskNeedsReport(BaseModel):
    """What placement decides on: the wire form of hivemind's TaskNeeds.

    Carried by cell.request when the Warden asks for any Cell that fits.
    """

    model_config = VALUE_MODEL_CONFIG

    isolation: IsolationNeed = Field(description="How much the task needs its own Cell.")
    needs_exoskeleton: bool = Field(
        description="Whether the task needs the virtual peripherals bundle (display, input, audio)."
    )
    os: OsFamily | None = Field(
        description="The operating system family the task needs; None when any will do."
    )
    network_scopes: tuple[Annotated[str, Field(max_length=MAX_NETWORK_SCOPE_CHARS)], ...] = Field(
        max_length=MAX_NETWORK_SCOPES,
        description="Network scopes the task must reach, in capability syntax.",
    )
    is_disposable: bool = Field(description="Whether the Cell may be destroyed when the task ends.")
    comb_shield: CombShieldLevel = Field(description="The least security tier the task needs.")
    tempo: Tempo = Field(description="The task's Tempo, which placement weighs distance against.")


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class CellReady(WaggleMessage):
    """Announce a Cell as booted, probed and, where its tier requires it, attested (cell.ready).

    Reports its platform, capabilities, access level, Comb Shield level and check results so
    placement can consider it. A NIGHT_VEIL Cell with any failed check is recorded on the trail
    but never schedulable, and the Queen checks comb_shield and access_level against its
    provisioning or enrolment record for cell_id: both are receiver rules.
    """

    cell_id: CellIdField = Field(description="The Cell that is ready.")
    warden_id: WardenIdField = Field(description="The Warden that owns this Cell from now on.")
    platform: PlatformReport = Field(
        description="OS, distribution, architecture, package manager, shell, Python."
    )
    capabilities: CellCapabilitiesReport = Field(description="What the Cell can do, as probed.")
    access_level: AccessLevel = Field(
        description="How much of the Cell the Hive may touch; FULL for every Virtual Cell."
    )
    comb_shield: CombShieldLevel = Field(
        description="The Cell's security tier, bound to the Cell not the task."
    )
    attestation: tuple[AttestationCheck, ...] = Field(
        max_length=MAX_ATTESTATION_CHECKS,
        description="Per-check results; empty exactly when comb_shield is MEADOW.",
    )
    runtime_version: str = Field(
        max_length=MAX_RUNTIME_VERSION_CHARS,
        description="The HiveMind runtime version in the Cell.",
    )

    @model_validator(mode="after")
    def _attestation_matches_tier(self) -> CellReady:
        """Require checks for a tiered Cell and none for a MEADOW one."""
        # MEADOW runs no attestation, so results on it were invented; PROPOLIS and NIGHT_VEIL
        # always run some, so an empty tuple means the Warden skipped them. Both directions are
        # checked so a tiered Cell can never slip into placement unattested.
        attests = self.comb_shield is not CombShieldLevel.MEADOW
        if attests != bool(self.attestation):
            raise ValueError(
                f"CellReady attestation must be non-empty exactly when comb_shield is not "
                f"MEADOW, got {self.comb_shield.value} with {len(self.attestation)} check(s)."
            )
        return self


class CellHeartbeat(WaggleMessage):
    """Report the Cell's own liveness: mode, leases, workers, shield state (cell.heartbeat).

    Distinct from the bee heartbeat, and it carries no capacity figures; the Queen watchdogs
    against interval_s. is_shield_verified is always true for a MEADOW Cell (a receiver rule).
    """

    cell_id: CellIdField = Field(description="The Cell reporting.")
    mode: CellMode = Field(description="The Cell's current mode.")
    lease_ids: tuple[LeaseIdField, ...] = Field(
        max_length=MAX_LEASE_IDS,
        description="Every lease open on the Cell, for orphan reconciliation.",
    )
    worker_count: int = Field(ge=0, description="Sub-bees running on the Cell.")
    is_shield_verified: bool = Field(
        description="Whether the tier's runtime checks held at this beat; always true for "
        "MEADOW (a receiver rule)."
    )
    interval_s: float = Field(gt=0, description="The sender's heartbeat cadence, in seconds.")
