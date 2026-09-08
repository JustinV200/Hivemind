"""Define the tool family's calling half: running one tool version and reporting its outcome.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The tool
family follows a tool from request to call; this module is the call. ``ToolInvoke`` runs one
promoted (or quarantining) tool version on a Cell (a unit of compute) with a JSON argument
document, through that Cell's Warden (its always-on supervisor) and its session; ``ToolResult``
answers with the call's bounded JSON output or a stable error code, and its duration. The
arguments and the output are the family's sanctioned JSON text on the wire: each is bounded,
proven to parse by ``waggle.messages.tool_json``, and validated against the tool's schema at the
edge, never handled as a mapping in transit. The authoring half (``ToolRequest``,
``ToolPromoted``) lives in ``waggle.messages.tool``, from which this module takes the version
shape; the split is by responsibility so each file stays under the codingrules 5.1 size limit.
Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by Workers and the Queen (the central orchestrator, for
    quarantine runs) and answered by Wardens; calls into waggle.messages.base,
    waggle.messages.labels, waggle.messages.tool and waggle.messages.tool_json only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A ToolResult that is not SUCCEEDED always carries an error code, and one that is never
      does, so the trail can tell a failure from a success without reading the reason.

See Also:
    - docs/waggle/spec.md section 8.8 for the normative fields, bounds and validators.
    - waggle.messages.tool for ToolRequest, ToolPromoted and the version shape.
    - waggle.messages.tool_json for the JSON checks on arguments_json and output_json.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, Field, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    TaskIdField,
    ToolIdField,
    WaggleMessage,
    WorkerIdField,
)
from waggle.messages.labels import HoneyClearance
from waggle.messages.tool import MAX_VERSION_CHARS, VERSION_PATTERN
from waggle.messages.tool_json import check_json_object_text, check_json_value_text

MAX_ARGUMENTS_CHARS = 65_536  # One call's argument document; 64 KiB holds any sane input.
MIN_TIMEOUT_S = 0.0  # A timeout is strictly greater than this; a zero budget could never run.
MAX_OUTPUT_CHARS = 131_072  # 128 KiB of output text; the Warden truncates past it and keeps Nectar.
MAX_ERROR_CODE_CHARS = 128  # A dotted hive.* code is a few short words; room for a deep path.
MIN_DURATION_S = 0.0  # Wall-clock time never runs backwards.

__all__ = [
    "MAX_ARGUMENTS_CHARS",
    "MAX_ERROR_CODE_CHARS",
    "MAX_OUTPUT_CHARS",
    "MIN_DURATION_S",
    "MIN_TIMEOUT_S",
    "ToolInvoke",
    "ToolInvokePurpose",
    "ToolOutcome",
    "ToolResult",
]


class ToolInvokePurpose(Enum):
    """Why a tool is called: ordinary work, or a quarantine test run."""

    WORK = "WORK"  # An ordinary granted call.
    QUARANTINE = "QUARANTINE"  # A test run under the quarantine capability, flight recorder on.


class ToolOutcome(Enum):
    """How a tool call ended."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"  # Schema, capability or OS re-validation refused it before it ran.
    TIMED_OUT = "TIMED_OUT"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# An immutable tool version, shaped exactly as ToolPromoted announces it.
_Version = Annotated[str, Field(max_length=MAX_VERSION_CHARS, pattern=VERSION_PATTERN)]
# A call's argument document: bounded first, then proven to parse as a JSON object.
_ArgumentsText = Annotated[
    str, Field(max_length=MAX_ARGUMENTS_CHARS), AfterValidator(check_json_object_text)
]
# A call's output: bounded first, then proven empty or parseable as any JSON value.
_OutputText = Annotated[
    str, Field(max_length=MAX_OUTPUT_CHARS), AfterValidator(check_json_value_text)
]


class ToolInvoke(WaggleMessage):
    """Run one tool version on a Cell with a JSON argument document (tool.invoke, a request).

    From a Worker to its Warden, or from the Queen to a Warden for a quarantine run; answered
    by a ToolResult.
    """

    tool_id: ToolIdField = Field(description="The tool to run.")
    version: _Version = Field(description="The exact version.")
    cell_id: CellIdField = Field(
        description="The Cell whose Warden runs it; must be the recipient Warden's Cell "
        "(receiver rule).",
    )
    task_id: TaskIdField | None = Field(
        description="The task the call serves; None for a quarantine run."
    )
    worker_id: WorkerIdField | None = Field(
        description="The calling Worker, whose capability set is checked; None when the Queen "
        "drives a quarantine run. When the envelope sender is a Worker it must equal it and "
        "purpose must be WORK; QUARANTINE and a None worker_id are accepted only from a hive "
        "sender (receiver rule), so no Worker can borrow the quarantine capability.",
    )
    purpose: ToolInvokePurpose = Field(description="Work or quarantine.")
    arguments_json: _ArgumentsText = Field(
        description="The call's input as a JSON object document matching the tool's schema."
    )
    timeout_s: float = Field(
        gt=MIN_TIMEOUT_S,
        description="Seconds before the run is killed and reported TIMED_OUT.",
    )
    reason: _Reason = Field(description="Why this tool is called now.")


class ToolResult(WaggleMessage):
    """Return one ToolInvoke's outcome: bounded JSON output or a stable error code (tool.result).

    A reply from the Warden to the calling Worker, or to the Queen for a quarantine run.
    """

    tool_id: ToolIdField = Field(description="The tool that ran.")
    version: _Version = Field(description="The version that ran.")
    outcome: ToolOutcome = Field(description="The result.")
    output_json: _OutputText = Field(
        description="The output as JSON text; empty when there is none. The Warden truncates "
        "with is_truncated until the frame fits.",
    )
    is_truncated: bool = Field(
        default=False,
        description="True when the output was cut at the cap; the Warden keeps the whole as "
        "Nectar.",
    )
    clearance: HoneyClearance = Field(
        description="The label of output_json, from the task's clearance."
    )
    error_code: Annotated[str, Field(max_length=MAX_ERROR_CODE_CHARS)] | None = Field(
        description="A stable hive.* code for a failure or denial; set exactly when outcome is "
        "not SUCCEEDED.",
    )
    duration_s: float = Field(ge=MIN_DURATION_S, description="Wall-clock seconds.")
    reason: _Reason = Field(
        description="The explanation of a non-success, or a one-line summary on success."
    )

    @model_validator(mode="after")
    def _error_code_exactly_on_non_success(self) -> ToolResult:
        """Require an error code for every outcome but SUCCEEDED, and refuse one on success."""
        # A failure without a code cannot be handled by anything but a human reading prose; a
        # success with a code is a contradiction the trail would record as both.
        succeeded = self.outcome is ToolOutcome.SUCCEEDED
        if succeeded == (self.error_code is not None):
            raise ValueError(
                f"ToolResult error_code is set exactly when outcome is not SUCCEEDED, got "
                f"outcome {self.outcome.value} with error_code {self.error_code}."
            )
        return self
