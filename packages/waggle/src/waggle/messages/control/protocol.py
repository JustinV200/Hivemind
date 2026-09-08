"""Define the control family's protocol messages: liveness, the error reply, stop, cluster, wake.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
control family is the part of it that is about the protocol and the Hive itself rather than
about any one task. The six messages here are its protocol half: liveness probes (``Ping``,
``Pong``), the error reply every receiver can send (``ErrorMessage``, spec section 7), the order
to stop (``Shutdown``), and Clustering (pausing and preserving every bee that depends on a model
provider while that provider is down: ``Cluster`` to start it, ``Wake`` to end it). The family's
Hive-wide half (a human's message, a Pheromone Mask override, the Hive Stand's relocation
notice) lives in ``waggle.messages.control.hive``, split out by responsibility so each file
stays under the codingrules 5.1 size limit. Shutdown, Cluster and Wake are sent only by the
Queen (the central orchestrator) to a Warden (the always-on supervisor of one Cell, a unit of
compute); a receiver enforces that as a first-hop rule (spec section 3). Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built and read by the Queen, every Warden and every Pollen Packet
    (the thin gateway on an enrolled device); calls into waggle.messages.base and
    waggle.messages.labels only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.

See Also:
    - docs/waggle/spec.md section 8.11 for the normative fields, bounds and validators.
    - docs/waggle/spec.md section 7 for the error shape and the stable code table.
    - waggle.messages.control.hive for HumanMessage, MaskOverride and QueenMoved.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import KIND_PATTERN, MAX_REASON_CHARS, UtcDatetime, WaggleMessage
from waggle.messages.labels import Urgency

MAX_ERROR_CODE_CHARS = 128  # A dotted code is a few short words; room for a deep subsystem path.
ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$"  # waggle.*, hive.*, pollen.*: dotted.
MIN_ERROR_MESSAGE_CHARS = 1  # An error always says something.
MAX_ERROR_MESSAGE_CHARS = 2_000  # A full sentence or two with the ids to debug; never a traceback.
MAX_FAILED_KIND_CHARS = 64  # <family>.<snake_name>; the longest registered kind is far shorter.
MAX_PROVIDER_CHARS = 64  # A manifest provider key (anthropic, ollama_local, ...).
PROVIDER_PATTERN = r"^[a-z][a-z0-9_]*$"  # The shape of a manifest provider key.
MIN_DEADLINE_S = 0.0  # A shutdown deadline is never negative; IMMEDIATE pins it to exactly 0.

__all__ = [
    "ERROR_CODE_PATTERN",
    "MAX_ERROR_CODE_CHARS",
    "MAX_ERROR_MESSAGE_CHARS",
    "MAX_FAILED_KIND_CHARS",
    "MAX_PROVIDER_CHARS",
    "MIN_DEADLINE_S",
    "MIN_ERROR_MESSAGE_CHARS",
    "PROVIDER_PATTERN",
    "Cluster",
    "ClusterCause",
    "ErrorMessage",
    "Ping",
    "Pong",
    "Shutdown",
    "Wake",
]


class ClusterCause(Enum):
    """Why the Hive clusters (pauses and preserves its bees)."""

    PROVIDER_DOWN = "PROVIDER_DOWN"
    COST_CAP = "COST_CAP"
    HUMAN = "HUMAN"
    SUPERSEDURE = "SUPERSEDURE"  # The Hive Stand is moving to another machine.
    OFFLINE_LIMIT = "OFFLINE_LIMIT"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A provider name as the manifest keys it; shared by Cluster and Wake.
_Provider = Annotated[str, Field(max_length=MAX_PROVIDER_CHARS, pattern=PROVIDER_PATTERN)]
# The kind of a failed message, when an ErrorMessage can name it.
_FailedKind = Annotated[str, Field(max_length=MAX_FAILED_KIND_CHARS, pattern=KIND_PATTERN)]


class Ping(WaggleMessage):
    """Probe a peer's liveness and round trip (control.ping, a request).

    Carries nothing: the envelope's id and sent_at plus the Pong's correlation give the
    measurement, and the envelope's version advertises the sender's minor.
    """


class Pong(WaggleMessage):
    """Answer a Ping with the responder's arrival time (control.pong, a reply)."""

    received_at: UtcDatetime = Field(
        description="The responder's clock when the Ping arrived, so the pinger can estimate "
        "round trip and clock skew."
    )


class ErrorMessage(WaggleMessage):
    """Report that a received message could not be processed (control.error, a reply).

    Correlated to the message that failed; carries a stable code, a full sentence and whether
    the sender may retry (spec section 7). A traceback never crosses the wire, and the message
    names ids, never a path, hostname or content.
    """

    code: str = Field(
        max_length=MAX_ERROR_CODE_CHARS,
        pattern=ERROR_CODE_PATTERN,
        description="The stable code: waggle.* for protocol failures, hive.* or pollen.* for "
        "subsystem failures.",
    )
    message: str = Field(
        min_length=MIN_ERROR_MESSAGE_CHARS,
        max_length=MAX_ERROR_MESSAGE_CHARS,
        description="A full sentence with the identifiers needed to debug; never a traceback.",
    )
    failed_kind: _FailedKind | None = Field(
        description="The kind of the message that failed, for when the sender no longer holds "
        "the correlated envelope."
    )
    is_retryable: bool = Field(description="Whether resending the original may succeed.")


class Shutdown(WaggleMessage):
    """Order the recipient to stop, gracefully by a deadline or immediately (control.shutdown)."""

    urgency: Urgency = Field(
        description="GRACEFUL: checkpoint, release leases and stop by the deadline. IMMEDIATE: "
        "kill now, no checkpoint."
    )
    deadline_s: float = Field(
        ge=MIN_DEADLINE_S,
        description="Seconds allowed to checkpoint, release leases and exit; 0 when IMMEDIATE.",
    )
    reason: _Reason = Field(description="Why the recipient is stopped.")

    @model_validator(mode="after")
    def _immediate_has_no_deadline(self) -> Shutdown:
        """Reject an IMMEDIATE shutdown that grants time."""
        # IMMEDIATE means kill now (Sting Cut, Absconding); a deadline on it would let a Warden
        # read the order as graceful, so the two fields must agree.
        if self.urgency is Urgency.IMMEDIATE and self.deadline_s != MIN_DEADLINE_S:
            raise ValueError(
                f"An IMMEDIATE shutdown must carry deadline_s {MIN_DEADLINE_S}, got "
                f"{self.deadline_s}."
            )
        return self


class Cluster(WaggleMessage):
    """Tell a Warden to run the Clustering protocol for one provider or all (control.cluster).

    Checkpoint the affected sub-bees, pause their tasks, keep leases alive and heartbeats
    running. The provider is data here, never a branch.
    """

    provider: _Provider | None = Field(
        description="The manifest provider name whose bindings are unavailable; None means "
        "every provider (a full freeze)."
    )
    cause: ClusterCause = Field(description="Why the Hive clusters.")
    reason: _Reason = Field(description="The decision in prose.")


class Wake(WaggleMessage):
    """End Clustering for one provider or all so paused sub-bees resume (control.wake)."""

    provider: _Provider | None = Field(
        description="The provider that returned; None means every provider."
    )
    reason: _Reason = Field(description="Why the Hive wakes.")
