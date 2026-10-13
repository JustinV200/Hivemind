"""Define the tool family's quarantine half: the order to prove a tool, its report, its retirement.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The tool
family follows a tool from request to call; this module covers three messages that do not fit the
authoring or calling halves. ``ToolQuarantine`` asks a Warden (the always-on supervisor of one
Cell, a unit of compute) to run the Quarantine Comb (the sandbox every new tool must pass) over a
package the Basket (the Hive's store of signed blobs) already delivered: from a Warden to the
Queen (the central orchestrator) it is that Warden's request for a sandbox, from the Queen to the
sandbox's Warden it is the order to run the Comb there. ``ToolQuarantineReport`` answers it to
whoever asked, carrying the verdict and the full ``QuarantineReport`` as JSON text. ``ToolRetired``
announces that a version left the Comb Registry (the catalogue of promoted tools), which a rollback
cannot express because a rollback promotes an earlier version instead. The package itself never
rides these messages: it is a Basket blob named by ``package_sha256``. The JSON fields are bounded
and checked by ``waggle.messages.tool.json_text``, and the authoring half supplies the shared
scope, path and version shapes; the split is by responsibility so each file stays under the
codingrules 5.1 size limit. Every bound is a named constant here; the number, not the name, is
normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by Wardens (sandbox requests, cell-scope retirements, reports)
    and by the Queen (orders, hive-scope retirements); calls into waggle.messages.base,
    waggle.messages.tool.authoring and waggle.messages.tool.json_text only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - A CELL-scope message always names its Cell and a HIVE-scope one never does, so a receiver
      can tell the two apart without the registry.
    - A report names a failing gate exactly when it did not pass, so the trail can tell a pass
      from a failure without reading the reason.

See Also:
    - docs/waggle/spec.md section 8.8 for the normative fields, bounds and validators.
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for why the Comb runs where its
      sandbox is, driven by that Cell's Warden.
    - waggle.messages.tool.authoring for ToolScope, QuarantinePath and the version shape.
    - waggle.messages.tool.json_text for the JSON-object check on the three JSON fields.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field, model_validator

from waggle.ids import HiveId, IdKind, WardenId
from waggle.messages.base import (
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    ToolIdField,
    WaggleMessage,
    WardenIdField,
    id_validator,
)
from waggle.messages.tool.authoring import (
    MAX_VERSION_CHARS,
    MIN_PACKAGE_SIZE_BYTES,
    TOOL_NAME_PATTERN,
    VERSION_PATTERN,
    QuarantinePath,
    ToolScope,
)
from waggle.messages.tool.json_text import check_json_object_text

MAX_SPEC_CHARS = (
    32_768  # A ToolSpec as JSON text: a 16 KiB schema escaped inside it, plus the rest.
)
MAX_LIMITS_CHARS = 16_384  # The QuarantineLimits as JSON text: a handful of numbers, with room.
MAX_REPORT_CHARS = 65_536  # A QuarantineReport as JSON text: six gates and their bounded detail.
GATE_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"  # A gate's snake_case name: static, tests, fuzz.

__all__ = [
    "GATE_NAME_PATTERN",
    "MAX_LIMITS_CHARS",
    "MAX_REPORT_CHARS",
    "MAX_SPEC_CHARS",
    "ToolQuarantine",
    "ToolQuarantineReport",
    "ToolRetired",
]

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A tool name: the capability string tool:<name> once promoted.
_ToolName = Annotated[str, Field(pattern=TOOL_NAME_PATTERN)]
# An immutable tool version, shaped exactly as ToolPromoted announces it.
_Version = Annotated[str, Field(max_length=MAX_VERSION_CHARS, pattern=VERSION_PATTERN)]
# JSON object text, bounded first and then proven to parse as an object, at three different sizes.
_SpecText = Annotated[str, Field(max_length=MAX_SPEC_CHARS), AfterValidator(check_json_object_text)]
_LimitsText = Annotated[
    str, Field(max_length=MAX_LIMITS_CHARS), AfterValidator(check_json_object_text)
]
_ReportText = Annotated[
    str, Field(max_length=MAX_REPORT_CHARS), AfterValidator(check_json_object_text)
]
# The bee that retired a tool: the Queen (hive) or the owning Warden, the only two principals the
# Comb Registry lets retire anything.
_RetiredBy = Annotated[HiveId | WardenId, id_validator(IdKind.HIVE, IdKind.WARDEN)]


def _check_cell_matches_scope(message: str, scope: ToolScope, cell_id: str | None) -> None:
    """Raise unless ``cell_id`` is set exactly when ``scope`` is CELL."""
    # A CELL-scope tool without a Cell is visible nowhere; a HIVE-scope one naming a Cell would
    # let a Warden's tool masquerade as the Queen's. Checked in both directions, once for all
    # three messages so their wording cannot drift.
    if (scope is ToolScope.CELL) != (cell_id is not None):
        raise ValueError(
            f"{message} cell_id is required exactly when scope is CELL, got scope "
            f"{scope.value} with cell_id {cell_id}."
        )


class ToolQuarantine(WaggleMessage):
    """Order or request a Quarantine Comb run over a delivered package (tool.quarantine).

    A request. From a Warden to the Queen it is that Warden's sandbox request for a cell-scope
    tool: the Queen provisions the sandbox, runs the Comb there on the requester's behalf and
    relays the report back. From the Queen to a Warden it is the order to run the Comb on that
    Warden's own Cell, over the package she shipped to it as a Basket blob. Answered by a
    ToolQuarantineReport, or a control.error.
    """

    tool_id: ToolIdField = Field(description="The tool under test, as minted by its requester.")
    name: _ToolName = Field(description="The registry name the tool would take on promotion.")
    version: _Version = Field(description="The immutable version under test.")
    scope: ToolScope = Field(description="The visibility the tool would be promoted at.")
    cell_id: CellIdField | None = Field(
        description="The one Cell a CELL-scope tool is for; required exactly when scope is CELL.",
    )
    spec_json: _SpecText = Field(
        description="The ToolSpec as JSON object text; the Comb re-validates it and checks it "
        "against the package (receiver rule)."
    )
    package_sha256: str = Field(
        pattern=SHA256_PATTERN,
        description="Digest of the package, a Basket blob already resident on the recipient; "
        "the Comb unpacks exactly these bytes (receiver rule).",
    )
    package_size_bytes: int = Field(ge=MIN_PACKAGE_SIZE_BYTES, description="Size of that package.")
    path: QuarantinePath = Field(
        description="Which route through the Comb the sender chose: SANDBOX_CELL, or REAL_CELL "
        "for a platform the Ubuntu sandbox cannot exercise."
    )
    limits_json: _LimitsText = Field(
        description="The QuarantineLimits (CPU seconds, memory, wall time, output, fuzz cases) as "
        "JSON object text."
    )
    requested_by_warden: WardenIdField = Field(
        description="The Warden whose Worker or own work needs the tool; the report goes back to "
        "whoever sent this message, and this names the Warden for the trail."
    )
    reason: _Reason = Field(description="Why the tool is being quarantined now.")

    @model_validator(mode="after")
    def _cell_scope_names_its_cell(self) -> ToolQuarantine:
        """Require cell_id for CELL scope, and refuse it for HIVE scope."""
        _check_cell_matches_scope("ToolQuarantine", self.scope, self.cell_id)
        return self


class ToolQuarantineReport(WaggleMessage):
    """Return one Quarantine Comb run's verdict and report (tool.quarantine_report, a reply).

    From the Warden that ran the Comb to the Queen, or from the Queen to the Warden that asked
    for a sandbox: it goes back to whoever sent the ToolQuarantine. The registry promotes only on
    a passing report whose digests match the spec and package offered, so the full report
    travels as JSON text for the promoter to re-check.
    """

    tool_id: ToolIdField = Field(description="The tool that was quarantined.")
    version: _Version = Field(description="The version that was quarantined.")
    passed: bool = Field(description="True when every gate passed.")
    path: QuarantinePath = Field(description="Which route proved, or failed to prove, the tool.")
    sandbox_cell_id: CellIdField = Field(
        description="The Cell the Comb ran in (the sandbox Virtual Cell, or the Hive Stand's Cell "
        "for the development subprocess sandbox)."
    )
    failing_gate: Annotated[str, Field(pattern=GATE_NAME_PATTERN)] | None = Field(
        description="The first gate that failed (static, tests, schema, fuzz, resources, "
        "capabilities); None exactly when passed is true.",
    )
    report_json: _ReportText = Field(
        description="The full QuarantineReport as JSON object text: gates in order with their "
        "reasons, resource usage, digests and times."
    )
    reason: _Reason = Field(description="A one-line summary of the verdict for the trail.")

    @model_validator(mode="after")
    def _failing_gate_exactly_on_failure(self) -> ToolQuarantineReport:
        """Require a failing gate for a failed report, and refuse one on a pass."""
        # A failure that names no gate cannot be retried with a targeted fix, and a pass that
        # names a gate contradicts itself in the trail.
        if self.passed == (self.failing_gate is not None):
            raise ValueError(
                f"ToolQuarantineReport failing_gate is None exactly when passed, got passed "
                f"{self.passed} with failing_gate {self.failing_gate}."
            )
        return self


class ToolRetired(WaggleMessage):
    """Announce that a tool version left the Comb Registry (tool.retired, an event).

    From the Queen to Wardens, and from a Warden to its Workers, so they stop offering the
    version. A retired version is never deleted from the registry: its row and package stay as
    evidence, and a rollback may make an earlier version current again.
    """

    tool_id: ToolIdField = Field(description="The retired tool.")
    name: _ToolName = Field(description="The registry name.")
    version: _Version = Field(description="The immutable version retired.")
    scope: ToolScope = Field(description="The visibility the version had.")
    cell_id: CellIdField | None = Field(
        description="The one Cell a CELL-scope tool was visible on; required exactly when scope "
        "is CELL.",
    )
    retired_by: _RetiredBy = Field(
        description="The Queen's address, or the address of the Warden that owns a CELL-scope "
        "tool; no other bee may retire a tool (receiver rule)."
    )
    reason: _Reason = Field(description="Why the version was withdrawn.")

    @model_validator(mode="after")
    def _cell_scope_names_its_cell(self) -> ToolRetired:
        """Require cell_id for CELL scope, and refuse it for HIVE scope."""
        _check_cell_matches_scope("ToolRetired", self.scope, self.cell_id)
        return self
