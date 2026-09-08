"""Define the tool family's authoring half: asking for a tool and announcing its promotion.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The tool
family follows a tool from request to call: a bee asks for a capability it lacks, the Royal Jelly
Lab (the tool-authoring pipeline) scaffolds and quarantines it, the Comb Registry (the catalogue
of promoted tools) announces the promotion, and calls run through the Cell's Warden (the
always-on supervisor of one Cell, a unit of compute). This module is the authoring half:
``ToolRequest`` asks for a tool for the target Cell's platform, and ``ToolPromoted`` announces
that a version passed the Quarantine Comb (the sandbox every new tool must pass) and is now in
the registry at a scope. The calling half (``ToolInvoke``, ``ToolResult``) lives in
``waggle.messages.tool.call`` and the JSON-text checks in ``waggle.messages.tool.json_text``, split
out by responsibility so each file stays under the codingrules 5.1 size limit. No tool code or
package ever crosses Waggle; only a digest does. Every bound is a named constant here; the
number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by Workers and Wardens (requests) and by the Queen (the
    central orchestrator) or a Warden (promotions); calls into waggle.messages.base,
    waggle.messages.labels, waggle.messages.reports and waggle.messages.tool.json_text only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A CELL-scope promotion always names its Cell and its promoting Warden, and a HIVE-scope
      one never does, so a loader can tell the two apart without the registry.

See Also:
    - docs/waggle/spec.md section 8.8 for the normative fields, bounds and validators.
    - waggle.messages.tool.call for ToolInvoke and ToolResult, the family's other half.
    - waggle.messages.tool.json_text for the JSON-object check on input_schema_json.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, Field, field_validator, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    TaskIdField,
    ToolIdField,
    UtcDatetime,
    WaggleMessage,
    WardenIdField,
    WorkerIdField,
)
from waggle.messages.labels import OsFamily
from waggle.messages.reports import PlatformReport
from waggle.messages.tool.json_text import check_json_object_text

TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"  # A capability string tool:<name>, snake, 64 max.
MIN_DESCRIPTION_CHARS = 1  # A request with no brief gives the scaffolder nothing to build.
MAX_DESCRIPTION_CHARS = 4_000  # A brief or a model-facing description: a page, never a manual.
MAX_REQUIRED_CAPABILITIES = 32  # A tool needs a handful of scopes; more is a design smell.
MAX_CAPABILITY_CHARS = 128  # One capability string (net:host:example.org, tool:<name>).
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"  # Semantic version, three numbers, no prerelease tags.
MAX_VERSION_CHARS = 32  # Three numbers and two dots; room for absurd but legal counters.
MAX_INPUT_SCHEMA_CHARS = 16_384  # A JSON Schema for one tool's arguments; 16 KiB is generous.
MIN_SUPPORTED_OS = 1  # A tool quarantined on no platform was proven nowhere.
MIN_PACKAGE_SIZE_BYTES = 1  # An empty package holds no tool.

__all__ = [
    "MAX_CAPABILITY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_INPUT_SCHEMA_CHARS",
    "MAX_REQUIRED_CAPABILITIES",
    "MAX_VERSION_CHARS",
    "MIN_DESCRIPTION_CHARS",
    "MIN_PACKAGE_SIZE_BYTES",
    "MIN_SUPPORTED_OS",
    "TOOL_NAME_PATTERN",
    "VERSION_PATTERN",
    "QuarantinePath",
    "ToolPromoted",
    "ToolRequest",
    "ToolScope",
]


class ToolScope(Enum):
    """Who can see a promoted tool."""

    HIVE = "HIVE"  # Promoted by the Queen, visible everywhere.
    CELL = "CELL"  # Promoted by a Warden for its own Cell.


class QuarantinePath(Enum):
    """Which route through the Quarantine Comb proved a tool."""

    SANDBOX_CELL = "SANDBOX_CELL"  # Fully exercised in the sandbox Virtual Cell.
    REAL_CELL = "REAL_CELL"  # Static checks in the sandbox, tests in scratch on a Real Cell.


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A tool name: the capability string tool:<name> once promoted.
_ToolName = Annotated[str, Field(pattern=TOOL_NAME_PATTERN)]
# An immutable tool version, as ToolPromoted, ToolInvoke and ToolResult all carry it.
_Version = Annotated[str, Field(max_length=MAX_VERSION_CHARS, pattern=VERSION_PATTERN)]
# The capability strings a tool needs or a caller must hold.
_Capabilities = Annotated[
    tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...],
    Field(max_length=MAX_REQUIRED_CAPABILITIES),
]
# A JSON Schema document as text: bounded first, then proven to parse as a JSON object.
_SchemaText = Annotated[
    str, Field(max_length=MAX_INPUT_SCHEMA_CHARS), AfterValidator(check_json_object_text)
]


class ToolRequest(WaggleMessage):
    """Ask for a capability the requester lacks (tool.request, a request).

    From a Worker to its Warden and on to the Queen, so the Royal Jelly Lab scaffolds,
    quarantines and promotes a tool for the target Cell's platform. A refusal is a control.error.
    """

    tool_id: ToolIdField = Field(
        description="Minted by the first requester; names the tool through every stage and on "
        "the eventual ToolPromoted.",
    )
    warden_id: WardenIdField = Field(description="The Warden responsible for the requesting Cell.")
    worker_id: WorkerIdField | None = Field(
        description="The Worker whose task needs it, when a Worker started the request."
    )
    task_id: TaskIdField | None = Field(description="The task blocked on the missing capability.")
    cell_id: CellIdField = Field(description="The Cell the tool must run on.")
    scope: ToolScope = Field(description="Requested visibility.")
    name: _ToolName = Field(
        description="Desired tool name, the capability string tool:<name> once promoted."
    )
    description: str = Field(
        min_length=MIN_DESCRIPTION_CHARS,
        max_length=MAX_DESCRIPTION_CHARS,
        description="What the tool must do, the scaffolder's brief.",
    )
    required_capabilities: _Capabilities = Field(
        description="Capability strings the tool will need; anything beyond the requester's "
        "own set is rejected (receiver rule).",
    )
    platform: PlatformReport = Field(description="The target Cell's platform.")
    reason: _Reason = Field(description="Why the capability is needed now.")


class ToolPromoted(WaggleMessage):
    """Announce that a tool version is in the Comb Registry at a scope (tool.promoted, an event).

    From the Queen to Wardens and on to Workers, so they load it and the requester unblocks. A
    rollback is a promotion of an earlier version naming the one it displaces.
    """

    tool_id: ToolIdField = Field(description="The promoted tool.")
    name: _ToolName = Field(description="The registry name.")
    version: _Version = Field(description="The immutable version promoted.")
    scope: ToolScope = Field(description="Visibility, set at promotion.")
    cell_id: CellIdField | None = Field(
        description="The one Cell whose bees may see a CELL-scope tool; required exactly when "
        "scope is CELL.",
    )
    description: str = Field(
        max_length=MAX_DESCRIPTION_CHARS, description="The model-facing description."
    )
    input_schema_json: _SchemaText = Field(
        description="The input schema as JSON Schema text; must parse as a JSON object."
    )
    required_capabilities: _Capabilities = Field(description="Capabilities a caller must hold.")
    supported_os: tuple[OsFamily, ...] = Field(
        min_length=MIN_SUPPORTED_OS,
        description="Platforms the tool was written and quarantined for; unique.",
    )
    quarantine_path: QuarantinePath = Field(description="Which route proved it.")
    quarantined_on: CellIdField = Field(description="The Cell the quarantine report came from.")
    quarantine_passed_at: UtcDatetime = Field(description="When the report passed.")
    package_sha256: str = Field(
        pattern=SHA256_PATTERN,
        description="Digest of the immutable package in the registry, so a loader verifies "
        "what it installs by whatever channel delivers it.",
    )
    package_size_bytes: int = Field(ge=MIN_PACKAGE_SIZE_BYTES, description="Size of that package.")
    promoted_by_warden: WardenIdField | None = Field(
        description="The promoting Warden for a CELL-scope tool; None when the Queen promoted "
        "at HIVE scope. Required exactly when scope is CELL.",
    )
    supersedes_version: _Version | None = Field(
        description="The version this promotion replaces as current; None for a first promotion."
    )
    reason: _Reason = Field(description="Why it was promoted, the Queen's review note included.")

    @field_validator("supported_os")
    @classmethod
    def _supported_os_unique(cls, value: tuple[OsFamily, ...]) -> tuple[OsFamily, ...]:
        """Reject a platform listed twice."""
        # A repeated member is a sender bug; refusing it keeps the loader's set semantics honest.
        if len(set(value)) != len(value):
            raise ValueError("ToolPromoted supported_os must be unique.")
        return value

    @model_validator(mode="after")
    def _cell_scope_names_its_cell_and_warden(self) -> ToolPromoted:
        """Require cell_id and promoted_by_warden for CELL scope, and neither for HIVE scope."""
        # A CELL-scope tool without a Cell is visible nowhere and without a Warden was promoted
        # by nobody; a HIVE-scope tool naming either would let a Warden's promotion masquerade as
        # the Queen's. Both fields are checked in both directions.
        cell_scoped = self.scope is ToolScope.CELL
        if cell_scoped != (self.cell_id is not None):
            raise ValueError(
                f"ToolPromoted cell_id is required exactly when scope is CELL, got scope "
                f"{self.scope.value} with cell_id {self.cell_id}."
            )
        if cell_scoped != (self.promoted_by_warden is not None):
            raise ValueError(
                f"ToolPromoted promoted_by_warden is required exactly when scope is CELL, got "
                f"scope {self.scope.value} with promoted_by_warden {self.promoted_by_warden}."
            )
        return self
