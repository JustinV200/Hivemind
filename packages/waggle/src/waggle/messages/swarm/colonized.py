"""Define the swarm family's colonized-device messages: Nuc promotion, its outcome, trail sync.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the swarm
family is about devices joining and living in the Swarm (the mesh of enrolled Real Cells, existing
devices borrowed for tasks and left exactly as found). The three messages here concern a colonized
device, one whose Warden (the always-on supervisor of one Cell, a unit of compute) lives on it,
split out of ``waggle.messages.swarm.enrolment`` by responsibility so each file stays under the
codingrules 5.1 size limit. The Queen (the central orchestrator) orders that Warden to start or stop
a local model server with a ``NucPromote``, making the device a Nuc (a colonized Real Cell with its
own model server, which keeps working when disconnected) or an ordinary colonized Cell again, and
the Warden answers with a ``NucPromoted``. When such a Warden reconnects after working offline, it
ships its local segment of the Pheromone Trail (the append-only audit log, one logical log made of
per-node segments) to the Queen in ``TrailSegmentSync`` chunks, which the Queen merges idempotently.
Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device connector),
    inside the waggle package. Registered by waggle.messages.registry, which maps each class to its
    kind; built by the Queen (promote) or a Warden (promoted, trail sync) and read by the other;
    calls into waggle.messages.base and waggle.messages.swarm.enrolment (the family's enums) only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A model server is named by its manifest key only; its kind and address never cross the
      wire, so a Warden can only start what the operator's manifest already defines.
    - A trail chunk is at most MAX_CHUNK_BYTES and travels with offset and final (section 5).

See Also:
    - docs/waggle/spec.md section 8.10 for the normative fields, bounds and validators.
    - docs/waggle/spec.md section 5 (the chunking rule) for offset, final and the group key.
    - waggle.messages.swarm.enrolment for EnrolRequest, EnrolAccept, DeviceHeartbeat and the enums.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    MAX_CHUNK_BYTES,
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    DeviceIdField,
    EventIdField,
    NodeIdField,
    UtcDatetime,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.swarm.enrolment import NucAction, NucOutcome, RuntimeLevel

MAX_SERVER_ID_CHARS = 128  # A manifest key for a model server definition; one identifier.
MAX_MODELS = 32  # Models one server is asked to hold at once; VRAM runs out long before.
MAX_MODEL_ID_CHARS = 128  # A Forage map model id: a name and a tag, never a description.
MIN_EVENT_COUNT = 1  # A segment with no events would have nothing to merge.
MIN_SEGMENT_FORMAT_VERSION = 1  # Export formats are numbered from 1; 0 would mean "unknown".
MIN_SEGMENT_BYTES = 1  # An export of a non-empty segment is never empty.

__all__ = [
    "MAX_MODELS",
    "MAX_MODEL_ID_CHARS",
    "MAX_SERVER_ID_CHARS",
    "MIN_EVENT_COUNT",
    "MIN_SEGMENT_BYTES",
    "MIN_SEGMENT_FORMAT_VERSION",
    "NucPromote",
    "NucPromoted",
    "TrailSegmentSync",
]

# The success outcome each action can report; FAILED may answer either.
_SUCCESS_FOR_ACTION = {
    NucAction.PROMOTE: NucOutcome.PROMOTED,
    NucAction.DEMOTE: NucOutcome.DEMOTED,
}

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A manifest key naming a local model server definition; the receiver resolves it.
_ServerId = Annotated[str, Field(max_length=MAX_SERVER_ID_CHARS)]
# A bounded list of Forage map model ids, as both Nuc messages carry one.
_ModelIds = Annotated[
    tuple[Annotated[str, Field(max_length=MAX_MODEL_ID_CHARS)], ...], Field(max_length=MAX_MODELS)
]


class NucPromote(WaggleMessage):
    """Order a colonized device's Warden to start or stop a model server (swarm.nuc_promote).

    A request, within the ceilings and hosting plan already set: PROMOTE makes the device a
    Nuc, DEMOTE an ordinary colonized Cell again.
    """

    cell_id: CellIdField = Field(
        description="The Cell promoted or demoted; must be the recipient Warden's Cell."
    )
    device_id: DeviceIdField = Field(description="The device behind that Cell.")
    action: NucAction = Field(description="Promote or demote.")
    server_id: _ServerId | None = Field(
        description="The manifest key of the local model server definition to start; its kind "
        "and address come from the manifest, never the wire. Required for PROMOTE, None for "
        "DEMOTE."
    )
    initial_models: _ModelIds = Field(
        description="Forage map model ids to load first, each within the ceilings' allowlist "
        "and VRAM; empty for DEMOTE."
    )
    deadline_s: float = Field(
        gt=0, description="Seconds to bring the server up (or down) before replying FAILED."
    )
    reason: _Reason = Field(description="The Queen's decision.")

    @model_validator(mode="after")
    def _server_fields_match_action(self) -> NucPromote:
        """Require a server for PROMOTE, and neither a server nor models for DEMOTE."""
        # A promotion without a server definition could start nothing, and a demotion that
        # names a server or models would read as a promotion in disguise; both directions hold.
        promoting = self.action is NucAction.PROMOTE
        if (self.server_id is not None) != promoting:
            raise ValueError(
                f"NucPromote server_id is required exactly when action is PROMOTE, got action "
                f"{self.action.value} with server_id {self.server_id}."
            )
        if self.initial_models and not promoting:
            raise ValueError(
                f"NucPromote initial_models must be empty for DEMOTE, got "
                f"{len(self.initial_models)} model id(s)."
            )
        return self


class NucPromoted(WaggleMessage):
    """Report the outcome of a promotion or demotion (swarm.nuc_promoted, a reply).

    The device's new level, its server and the models now loaded; the detailed sources follow
    as a forage.capacity_report.
    """

    cell_id: CellIdField = Field(description="The Cell.")
    device_id: DeviceIdField = Field(description="The device behind it.")
    action: NucAction = Field(description="Echo of the request.")
    outcome: NucOutcome = Field(
        description="PROMOTED only with PROMOTE, DEMOTED only with DEMOTE; FAILED with either."
    )
    level: RuntimeLevel = Field(description="The rung after the attempt.")
    server_id: _ServerId | None = Field(
        description="The server now running; set exactly when outcome is PROMOTED."
    )
    loaded_models: _ModelIds = Field(
        description="Model ids now served; empty unless outcome is PROMOTED."
    )
    reason: _Reason = Field(description="What happened, especially why a promotion failed.")

    @model_validator(mode="after")
    def _outcome_matches_action(self) -> NucPromoted:
        """Allow PROMOTED only after a PROMOTE and DEMOTED only after a DEMOTE."""
        # FAILED may answer either action, but a success must name the action that succeeded:
        # a reply reporting a demotion to a promote order is a bug, never a state.
        expected = _SUCCESS_FOR_ACTION[self.action]
        if self.outcome is not NucOutcome.FAILED and self.outcome is not expected:
            raise ValueError(
                f"NucPromoted outcome {self.outcome.value} cannot answer action "
                f"{self.action.value}; only {expected.value} or FAILED can."
            )
        return self

    @model_validator(mode="after")
    def _server_fields_match_outcome(self) -> NucPromoted:
        """Require server_id exactly when PROMOTED, and allow loaded models only then."""
        # After anything but a successful promotion no server of the Hive's is running, so a
        # server id or a model list would describe something the Queen must not count on.
        promoted = self.outcome is NucOutcome.PROMOTED
        if (self.server_id is not None) != promoted:
            raise ValueError(
                f"NucPromoted server_id is set exactly when outcome is PROMOTED, got outcome "
                f"{self.outcome.value} with server_id {self.server_id}."
            )
        if self.loaded_models and not promoted:
            raise ValueError(
                f"NucPromoted loaded_models must be empty unless outcome is PROMOTED, got "
                f"{len(self.loaded_models)} model id(s) with outcome {self.outcome.value}."
            )
        return self


class TrailSegmentSync(WaggleMessage):
    """Ship one chunk of an offline Warden's trail segment (swarm.trail_segment_sync, an event).

    The Queen merges the segment idempotently on reconnection; the bytes are the trail's own
    export format, opaque to Waggle. The Queen dedupes by the envelope node_id, first_event_id,
    last_event_id and sha256 together.
    """

    node_id: NodeIdField = Field(
        description="The node whose segment this is. Must equal the envelope's node_id, and "
        "warden_id the envelope's sender (receiver rule, answered with control.error), so no "
        "Warden can sync a segment under another node's identity."
    )
    cell_id: CellIdField = Field(description="The Cell the Warden owns.")
    warden_id: WardenIdField = Field(description="The Warden that wrote it.")
    from_at: UtcDatetime = Field(description="Timestamp of the segment's first event.")
    to_at: UtcDatetime = Field(description="Timestamp of its last event; at least from_at.")
    first_event_id: EventIdField = Field(
        description="First event in the segment; with last_event_id and node_id it identifies "
        "the segment so a replayed sync is deduplicated."
    )
    last_event_id: EventIdField = Field(description="Last event in the segment.")
    event_count: int = Field(
        ge=MIN_EVENT_COUNT, description="Events in the whole segment, checked after merge."
    )
    segment_format_version: int = Field(
        ge=MIN_SEGMENT_FORMAT_VERSION,
        description="The export format version, so the merge can refuse an unknown layout.",
    )
    chunk: bytes = Field(max_length=MAX_CHUNK_BYTES, description="A slice of the exported segment.")
    offset: int = Field(ge=0, description="Byte offset within the export.")
    total_bytes: int = Field(ge=MIN_SEGMENT_BYTES, description="Size of the whole export.")
    final: bool = Field(
        default=False,
        description="True on the last chunk; the Queen then verifies and merges.",
    )
    sha256: Annotated[str, Field(pattern=SHA256_PATTERN)] | None = Field(
        description="Digest of the whole export; required when final, None otherwise."
    )

    @model_validator(mode="after")
    def _segment_ends_after_it_starts(self) -> TrailSegmentSync:
        """Reject a segment whose last event precedes its first."""
        # The two timestamps bound the segment for ordering across nodes; reversed they would
        # place the segment nowhere, so a sender that produced them has a broken export.
        if self.to_at < self.from_at:
            raise ValueError(
                f"TrailSegmentSync to_at {self.to_at.isoformat()} precedes from_at "
                f"{self.from_at.isoformat()}."
            )
        return self

    @model_validator(mode="after")
    def _digest_exactly_when_final(self) -> TrailSegmentSync:
        """Require the digest on the final chunk and forbid it on every other."""
        # The digest is what the Queen verifies the reassembled export against before merging,
        # so the closing chunk must carry it; earlier it could only describe an incomplete export.
        if self.final != (self.sha256 is not None):
            raise ValueError(
                f"TrailSegmentSync sha256 is required exactly when final, got final="
                f"{self.final} with sha256 {self.sha256}."
            )
        return self
