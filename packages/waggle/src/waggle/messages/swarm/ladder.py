"""Define the swarm family's ladder messages: colonize a device, report it, update its limits.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
swarm family is about devices joining and living in the Swarm (the mesh of enrolled Real Cells,
existing devices borrowed for tasks and left exactly as found). A device climbs a ladder of three
rungs (``RuntimeLevel``): level 0, where only its Pollen Packet (the thin gateway installed on it)
runs and its Warden (the always-on supervisor of its Cell, a unit of compute) lives on the Hive
Stand (the machine the Queen, the central orchestrator, runs on); level 1, colonized, where the
Warden and its sub-bees run on the device itself; and level 2, a Nuc, which adds a model server of
its own (``waggle.messages.swarm.colonized``). The three messages here are the first rung's
traffic. ``Colonize`` is the Queen's order to the packet, in three steps: PREPARE installs the
pinned runtime beside the packet, START launches the device's Warden under the same Warden id it
had on the Hive Stand, and WITHDRAW stops it again; ``Colonized`` is the packet's answer to each.
``NodeUpdate`` is how the Queen changes the standing terms of an enrolled device (its rung, access
level, Comb Shield tier, capability set and limits) without a new enrolment: the operator changed
a level, or a device moved up the ladder. Added in PROTOCOL_MINOR 13 (roadmap phase 11, ADR-0051
and ADR-0052). Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by the Queen (the order and the update) or a Pollen Packet (the
    answer) and read by the other; calls into waggle.messages.base, waggle.messages.labels and
    waggle.messages.swarm.enrolment (the rungs and the capability bounds) only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - Only the Queen sends a Colonize or a NodeUpdate (a receiver rule the packet applies), and
      a NodeUpdate, like an EnrolAccept, never grants NIGHT_VEIL, the virtual-only tier.
    - A Colonize carries no program, path or command: what runs on the device is the runtime
      the operator installed the packet with, so the wire can only start what is already there.

See Also:
    - docs/waggle/spec.md section 8.10 for the normative fields, bounds and validators.
    - waggle.messages.swarm.enrolment for RuntimeLevel and EnrolAccept, whose terms NodeUpdate
      amends.
    - waggle.messages.swarm.colonized for the second rung's traffic (the Nuc promotion).
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the migration these steps serve.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    DeviceIdField,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm.enrolment import (
    MAX_CAPABILITY_CHARS,
    MAX_GRANTED_CAPABILITIES,
    MAX_RUNTIME_VERSION_CHARS,
    RuntimeLevel,
)

MAX_WARDEN_URL_CHARS = 512  # A wss:// URL with host, port and path; longer is not an endpoint.
WARDEN_URL_PATTERN = r"^wss?://\S+$"  # A WebSocket scheme and no whitespace; waggle.uris rules on
# the rest (scheme against host, loopback or TLS), which the packet applies before it dials.
MIN_WARDEN_PID = 1  # Pid 0 is the scheduler or idle task on every platform, never a Warden.

__all__ = [
    "MAX_WARDEN_URL_CHARS",
    "MIN_WARDEN_PID",
    "WARDEN_URL_PATTERN",
    "Colonize",
    "ColonizeAction",
    "ColonizeOutcome",
    "Colonized",
    "NodeUpdate",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class ColonizeAction(Enum):
    """The step of colonization a Colonize orders; one message, one step."""

    PREPARE = "PREPARE"  # Install the pinned runtime beside the packet; start nothing.
    START = "START"  # Launch the device's Warden from the installed runtime.
    WITHDRAW = "WITHDRAW"  # Stop the device's Warden; the packet alone is left on the device.


class ColonizeOutcome(Enum):
    """How a colonization step ended, as its Colonized reports it."""

    PREPARED = "PREPARED"  # The runtime is installed and ready to start.
    STARTED = "STARTED"  # The Warden is running on the device.
    WITHDRAWN = "WITHDRAWN"  # The Warden has been stopped.
    FAILED = "FAILED"  # The step did not complete; the reason says why.


# The success outcome each action can report; FAILED may answer any of them.
_SUCCESS_FOR_ACTION = {
    ColonizeAction.PREPARE: ColonizeOutcome.PREPARED,
    ColonizeAction.START: ColonizeOutcome.STARTED,
    ColonizeAction.WITHDRAW: ColonizeOutcome.WITHDRAWN,
}
# The outcomes that leave a runtime version to report: one is installed or running.
_VERSIONED_OUTCOMES = frozenset({ColonizeOutcome.PREPARED, ColonizeOutcome.STARTED})

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A pinned HiveMind runtime version ("0.4.2" plus a pre-release or local tag at most).
_RuntimeVersion = Annotated[str, Field(min_length=1, max_length=MAX_RUNTIME_VERSION_CHARS)]


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class NodeUpdate(WaggleMessage):
    """Change an enrolled device's standing terms without a new enrolment (swarm.node_update).

    An event from the Queen to the device's Pollen Packet, which persists it and applies it from
    the next request it handles. It restates the whole set of terms an EnrolAccept granted that
    can change after enrolment, plus the rung the device is on, so the packet never has to merge.
    """

    device_id: DeviceIdField = Field(description="The device whose terms change.")
    cell_id: CellIdField = Field(description="The Real Cell this device is.")
    warden_id: WardenIdField = Field(
        description="The Cell's Warden, which keeps its id across every rung; the packet accepts "
        "session and lease traffic only from it or the Queen."
    )
    level: RuntimeLevel = Field(
        description="The rung the device is on, so the packet knows whether the dead-man switch "
        "or an on-device Warden handles a lost link."
    )
    access_level: AccessLevel = Field(description="The level now granted.")
    comb_shield: CombShieldLevel = Field(
        description="The tier now set by the operator; never NIGHT_VEIL, the virtual-only tier "
        "(validator)."
    )
    granted_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            max_length=MAX_GRANTED_CAPABILITIES,
            description="The node's capability strings from now on, checked locally on every "
            "request; replaces the previous set entirely.",
        )
    )
    offline_limit_s: float = Field(
        gt=0, description="The dead-man limit for a device with no Warden on it, from now on."
    )
    heartbeat_interval_s: float = Field(
        gt=0, description="How often the packet sends DeviceHeartbeat, from now on."
    )
    reason: _Reason = Field(description="Why the terms changed.")

    @field_validator("comb_shield")
    @classmethod
    def _never_night_veil(cls, value: CombShieldLevel) -> CombShieldLevel:
        """Reject the virtual-only tier on a Real Cell."""
        # The same rule as EnrolAccept's: NIGHT_VEIL is teardown-only, and a borrowed device can
        # never be torn down, so an update that named it could only mean a Queen-side bug.
        if value is CombShieldLevel.NIGHT_VEIL:
            raise ValueError(
                "NodeUpdate comb_shield is never NIGHT_VEIL: a Real Cell cannot hold the "
                "virtual-only tier."
            )
        return value


class Colonize(WaggleMessage):
    """Order one step of colonizing a device (swarm.colonize, a request).

    From the Queen to the device's Pollen Packet: PREPARE installs the pinned runtime, START
    launches the device's Warden and WITHDRAW stops it. The answer is a correlated Colonized
    (FAILED included), or a control.error when the packet refuses the order outright.
    """

    cell_id: CellIdField = Field(description="The Cell being colonized; the packet's own.")
    device_id: DeviceIdField = Field(description="The device behind that Cell.")
    action: ColonizeAction = Field(description="Which step: prepare, start or withdraw.")
    warden_id: WardenIdField = Field(
        description="The Warden the device will run: the id the Cell's Warden has had since "
        "enrolment, so it keeps its identity (and the trail its continuity) across the move."
    )
    runtime_version: _RuntimeVersion | None = Field(
        description="The HiveMind runtime version to install, which the packet installs from "
        "the wheelhouse or index its operator configured, never from the wire. Required for "
        "PREPARE; may accompany START to pin which prepared runtime starts; None for WITHDRAW "
        "(validator)."
    )
    warden_url: (
        Annotated[str, Field(max_length=MAX_WARDEN_URL_CHARS, pattern=WARDEN_URL_PATTERN)] | None
    ) = Field(
        description="The swarm listener's URL the started Warden dials. Required for START, "
        "None otherwise (validator). The packet checks it against waggle.uris before it passes "
        "it on."
    )
    deadline_s: float = Field(
        gt=0, description="Seconds the step may take before the packet answers FAILED."
    )
    reason: _Reason = Field(description="The Queen's decision.")

    @model_validator(mode="after")
    def _fields_match_action(self) -> Colonize:
        """Require a version for PREPARE, a URL for START, and neither where it means nothing."""
        # Each field belongs to one step: a PREPARE with no version installs nothing, a START
        # with no URL starts a Warden that cannot find the Queen, and a WITHDRAW that names
        # either would read as a restart in disguise. Both directions are checked.
        if (self.action is ColonizeAction.PREPARE) and self.runtime_version is None:
            raise ValueError("Colonize runtime_version is required when action is PREPARE.")
        if (self.action is ColonizeAction.WITHDRAW) and self.runtime_version is not None:
            raise ValueError("Colonize runtime_version must be None when action is WITHDRAW.")
        if (self.warden_url is not None) != (self.action is ColonizeAction.START):
            raise ValueError(
                f"Colonize warden_url is required exactly when action is START, got action "
                f"{self.action.value} with warden_url {self.warden_url}."
            )
        return self


class Colonized(WaggleMessage):
    """Report how a colonization step ended (swarm.colonized, a reply).

    The packet's answer to a Colonize: the step it did, whether it worked, the rung the device is
    on afterwards, and what is now installed or running. Correlated to the order it answers.
    """

    cell_id: CellIdField = Field(description="The Cell.")
    device_id: DeviceIdField = Field(description="The device behind it.")
    action: ColonizeAction = Field(description="Echo of the order's step.")
    outcome: ColonizeOutcome = Field(
        description="PREPARED only after PREPARE, STARTED only after START, WITHDRAWN only after "
        "WITHDRAW; FAILED after any (validator)."
    )
    level: RuntimeLevel = Field(description="The rung the device is on after the step.")
    runtime_version: _RuntimeVersion | None = Field(
        description="The runtime version now installed (PREPARED) or running (STARTED); set "
        "exactly then (validator)."
    )
    warden_pid: Annotated[int, Field(ge=MIN_WARDEN_PID)] | None = Field(
        description="The started Warden's process id on the device, for the packet's own "
        "supervision of it; set exactly when outcome is STARTED (validator)."
    )
    reason: _Reason = Field(description="What happened, especially why a step failed.")

    @model_validator(mode="after")
    def _outcome_matches_action(self) -> Colonized:
        """Allow each success outcome only after the step that produces it."""
        # FAILED may answer any step, but a success must name the step that succeeded: a reply
        # reporting a started Warden to a prepare order is a bug, never a state.
        expected = _SUCCESS_FOR_ACTION[self.action]
        if self.outcome is not ColonizeOutcome.FAILED and self.outcome is not expected:
            raise ValueError(
                f"Colonized outcome {self.outcome.value} cannot answer action "
                f"{self.action.value}; only {expected.value} or FAILED can."
            )
        return self

    @model_validator(mode="after")
    def _installed_fields_match_outcome(self) -> Colonized:
        """Require the version after PREPARED or STARTED, and the pid exactly after STARTED."""
        # After a failure or a withdrawal the Queen must not count on a runtime or a process the
        # reply cannot vouch for, and after a start she needs the pid to supervise.
        has_version = self.outcome in _VERSIONED_OUTCOMES
        if (self.runtime_version is not None) != has_version:
            raise ValueError(
                f"Colonized runtime_version is set exactly when outcome is PREPARED or STARTED, "
                f"got outcome {self.outcome.value} with runtime_version {self.runtime_version}."
            )
        if (self.warden_pid is not None) != (self.outcome is ColonizeOutcome.STARTED):
            raise ValueError(
                f"Colonized warden_pid is set exactly when outcome is STARTED, got outcome "
                f"{self.outcome.value} with warden_pid {self.warden_pid}."
            )
        return self
