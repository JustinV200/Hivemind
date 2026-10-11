"""Define the forage family's source messages: a model source added, removed, or ordered evicted.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Forage is
the Hive's capacity as data, and its map lists every source (a model on a server or a hosted
provider) that can serve a call. A Cell's Warden (its always-on supervisor) may start a model
server on its own Cell and load a source on it within the ceilings the Queen (the central
orchestrator) set once, so the Queen learns of such a source from the Warden, not from a plan she
wrote. ``SourceAdded`` is the Warden telling her a source is now loaded, with the figures she
needs to enter it in her Forage map and seat ledger; ``SourceRemoved`` is the Warden telling her
a source is gone, and why (``SourceRemovalCause``); ``SourceEvict`` is the one order running the
other way: the Queen asks a Warden to give back seats she borrowed from it for other Cells. The
family's capacity reports (``waggle.messages.forage.hosting``) stay the full snapshot; these three
are the deltas between snapshots, so a map entry appears and disappears the moment it happens.
Added in PROTOCOL_MINOR 11 (roadmap step 8.3, ADR-0044). Every bound is a named constant here;
the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry under
    ``forage.source_added``, ``forage.source_removed`` and ``forage.source_evict``; the first two
    are built by a Warden and read by the Queen, the third is built by the Queen and read by a
    Warden; calls into waggle.messages.base, waggle.messages.forage.capacity (the server id
    bound) and waggle.messages.forage.values (SourceRef and the grade bounds) only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - A Warden evicts only a source whose seats it exported (``Ceilings.exportable_seats``);
      for any other source it ignores the order, and the Queen's own ``forage.source_removed``
      never arrives. The Queen sends the order only to reclaim seats she exported (codingrules
      8.10). Both halves are receiver rules, deliberately absent from the validators.

See Also:
    - docs/waggle/spec.md section 8.4 for the normative fields and the receiver rule.
    - docs/adr/0044 and .claude/codingrules.md section 8.10 for why the Warden loads autonomously.
    - waggle.messages.forage.hosting for CapacityReport, the full snapshot these deltas follow.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field

from waggle.messages.base import MAX_REASON_CHARS, CellIdField, WaggleMessage
from waggle.messages.forage.capacity import MAX_SERVER_ID_CHARS
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE, SourceRef

MAX_SOURCE_CAPABILITIES = 16  # Capability flags on one source (tools, vision, json, ...).
MAX_CAPABILITY_CHARS = 32  # One flag: a short word, never a sentence.

__all__ = [
    "MAX_CAPABILITY_CHARS",
    "MAX_SOURCE_CAPABILITIES",
    "SourceAdded",
    "SourceEvict",
    "SourceRemovalCause",
    "SourceRemoved",
]


class SourceRemovalCause(Enum):
    """Why a source left a Cell's model server (forage.source_removed)."""

    UNLOADED = "UNLOADED"  # The Warden unloaded the model; the server kept running.
    SERVER_STOPPED = "SERVER_STOPPED"  # The Warden stopped the server, taking its sources along.
    SERVER_DIED = "SERVER_DIED"  # The server exited or stopped answering its health check.
    EVICTED = "EVICTED"  # The Warden obeyed a forage.source_evict to give seats back.
    LEASE_RELEASED = "LEASE_RELEASED"  # The Cell's lease ended, so its servers went with it.


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class SourceAdded(WaggleMessage):
    """Announce a model source as loaded on a Cell's server (forage.source_added, an event).

    Sent by the Cell's Warden after it loaded the model within its ceilings, with the figures
    the Queen needs to enter the source in her Forage map and seat ledger; the grade is the
    hand-set one from the Warden's manifest entry, never a measurement.
    """

    cell_id: CellIdField = Field(description="The Cell whose server now serves the source.")
    server_id: str = Field(
        max_length=MAX_SERVER_ID_CHARS,
        description="The manifest key ([llm.servers]) of the model server, as "
        "ModelServerReport.server_id.",
    )
    source: SourceRef = Field(description="The source that was loaded.")
    grade: int = Field(
        ge=MIN_MODEL_GRADE,
        le=MAX_MODEL_GRADE,
        description="The hand-set grade, 1 to 5, the Warden read from its manifest entry.",
    )
    context_window: int = Field(gt=0, description="The model's context window, in tokens.")
    seats_total: int = Field(ge=0, description="Concurrent requests the server allows for it.")
    vram_bytes: int = Field(ge=0, description="VRAM the loaded model occupies.")
    capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = Field(
        max_length=MAX_SOURCE_CAPABILITIES,
        description="The source's capability flags, as the manifest names them.",
    )
    reason: _Reason = Field(description="Why it was loaded, within which ceilings.")


class SourceRemoved(WaggleMessage):
    """Announce a model source as gone from a Cell's server (forage.source_removed, an event).

    Sent by the Cell's Warden so the Queen drops the source from her Forage map and releases
    the seats she had reserved on it.
    """

    cell_id: CellIdField = Field(description="The Cell whose server served the source.")
    server_id: str = Field(
        max_length=MAX_SERVER_ID_CHARS,
        description="The manifest key ([llm.servers]) of the model server, as "
        "ModelServerReport.server_id.",
    )
    source: SourceRef = Field(description="The source that was removed.")
    cause: SourceRemovalCause = Field(description="Which event removed it.")
    reason: _Reason = Field(description="Why, in the Warden's words.")


class SourceEvict(WaggleMessage):
    """Order a Warden to give back seats she exported (forage.source_evict, an order).

    The Queen sends it only to reclaim seats she borrowed from this Cell's server. A Warden
    evicts only when the named source is one whose seats it exported
    (``Ceilings.exportable_seats``); otherwise it ignores the order. After evicting, the Warden
    sends ``forage.source_removed`` with cause EVICTED.
    """

    cell_id: CellIdField = Field(description="The Cell whose server serves the source.")
    source: SourceRef = Field(description="The source to evict.")
    seats_to_reclaim: int = Field(
        gt=0, description="Seats the Queen needs back; at least 1, or there is nothing to order."
    )
    reason: _Reason = Field(description="Why the Queen needs the seats back.")
