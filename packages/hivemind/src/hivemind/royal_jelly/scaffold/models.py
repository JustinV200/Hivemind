"""Define the scaffolder's values: the brief it is given, the reply it gets and the result it makes.

The Royal Jelly Lab (the Hive's tool-authoring pipeline) turns a bee's request for a missing
capability into a candidate tool. `ToolRequestBrief` is everything the scaffolder may know: the
facts the Hive itself decided (tool id, requester, name, scope, Cell, target platform, the
capabilities the requester holds) plus the requester's free-text brief and, on the one retry, the
Quarantine Comb's report on the first attempt. `ScaffoldReply` is the JSON object the model fills:
its fields are untrusted until `ModelScaffolder` has validated them. `ScaffoldResult` is the
outcome: a validated `ToolSpec`, the three files, and the package bytes that name the version by
hash.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.scaffold. The
    brief is built by the Queen's tool job or a Warden from a `ToolRequest`; the reply crosses the
    model boundary inside `hivemind.llm.complete_structured`; the result goes to the promotion
    flow, which hands the package to the Quarantine Comb. Calls into the spec package, the
    Quarantine report and waggle's authoring bounds only.

Key invariants:
    - Every identity field of a spec (name, scope, Cell, tool id, requester) comes from the brief,
      never from the model: the reply has no field for any of them.
    - A reply that is not a closed object of the declared shape is refused by pydantic, so the
      ladder retries it before any of it is trusted.
    - `ScaffoldResult.files` holds exactly `tool.py`, `test_tool.py` and `spec.json`, and
      `package_sha256` is the hash of `package`.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for what a spec may ask for.
    - hivemind.royal_jelly.scaffold.scaffolder for the code that builds these values.
    - hivemind.llm.prompts (scaffold_tool.md) for the prompt that describes `ScaffoldReply`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.guard import CapabilitySet, InvalidCapabilityError
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from waggle.messages.base import MAX_REASON_CHARS, CellIdField, ToolIdField, WardenIdField
from waggle.messages.reports import PlatformReport
from waggle.messages.tool.authoring import (
    MAX_CAPABILITY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_INPUT_SCHEMA_CHARS,
    MAX_REQUIRED_CAPABILITIES,
    MAX_VERSION_CHARS,
    MIN_DESCRIPTION_CHARS,
    TOOL_NAME_PATTERN,
    VERSION_PATTERN,
    ToolRequest,
    ToolScope,
)
from waggle.messages.tool.json_text import check_json_object_text

MAX_SOURCE_CHARS = (
    120_000  # One source file a model writes: far above any sane tool, below the cap.
)
MAX_NOTES_CHARS = 1_000  # The model's own remark about its work: a sentence or two.
MAX_PREVIOUS_SOURCE_CHARS = 24_000  # The earlier tool.py shown on a retry: enough to fix, bounded.

__all__ = [
    "MAX_NOTES_CHARS",
    "MAX_PREVIOUS_SOURCE_CHARS",
    "MAX_SOURCE_CHARS",
    "ScaffoldReply",
    "ScaffoldResult",
    "ToolRequestBrief",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # codingrules 8.5.


class ToolRequestBrief(BaseModel):
    """What the scaffolder is asked to build: the Hive's decided facts plus the requester's brief.

    Crosses no wire itself; it is built in process from a `ToolRequest` (`from_request`) by
    whoever runs the promotion flow. Everything here that a model will read is untrusted text
    (`description`, `reason`, a previous report's detail) and is delimited in the prompt.
    """

    model_config = _MODEL_CONFIG

    tool_id: ToolIdField = Field(description="Minted by the first requester; names the tool.")
    requested_by: WardenIdField = Field(
        description="The Warden whose grant bounds the capabilities the tool may ask for."
    )
    name: str = Field(
        pattern=TOOL_NAME_PATTERN, description="The tool's name, decided by the Hive."
    )
    description: str = Field(
        min_length=MIN_DESCRIPTION_CHARS,
        max_length=MAX_DESCRIPTION_CHARS,
        description="What the tool must do, in the requester's words.",
    )
    required_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            max_length=MAX_REQUIRED_CAPABILITIES,
            description="The most the tool may declare: the requester's own capability strings.",
        )
    )
    platform: PlatformReport = Field(
        description="The target Cell's platform the tool is written for."
    )
    scope: ToolScope = Field(description="HIVE (everywhere) or CELL (one Cell's bees only).")
    cell_id: CellIdField | None = Field(
        default=None, description="The one Cell a CELL-scope tool is for; None for HIVE."
    )
    reason: str = Field(
        default="", max_length=MAX_REASON_CHARS, description="Why the capability is needed now."
    )
    previous_report: QuarantineReport | None = Field(
        default=None,
        description="The Quarantine Comb's report on the first attempt, set only on the retry.",
    )
    previous_tool_source: str | None = Field(
        default=None,
        max_length=MAX_PREVIOUS_SOURCE_CHARS,
        description="The first attempt's `tool.py`, shown on the retry so the fix is targeted.",
    )

    @field_validator("required_capabilities")
    @classmethod
    def _capabilities_parse(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse a capability string that does not parse, so the scaffolder can trust the set."""
        # The scaffolder builds a CapabilitySet from these; a typo here would otherwise surface
        # as a crash deep inside the call instead of a refusal at the door.
        try:
            CapabilitySet.parse(*value)
        except InvalidCapabilityError as exc:
            raise ValueError(f"required_capabilities holds an unparseable entry: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _cell_scope_names_its_cell(self) -> Self:
        """Require cell_id exactly when scope is CELL, the rule `ToolSpec` also enforces."""
        # Checked here so a bad brief fails at construction, not after a model call was paid for.
        if (self.scope is ToolScope.CELL) != (self.cell_id is not None):
            raise ValueError(
                f"ToolRequestBrief cell_id is required exactly when scope is CELL, got scope "
                f"{self.scope.value} with cell_id {self.cell_id}."
            )
        return self

    @classmethod
    def from_request(
        cls, request: ToolRequest, held: tuple[str, ...] | None = None
    ) -> ToolRequestBrief:
        """Build the brief for a `ToolRequest`, taking the requester's capabilities from `held`.

        Args:
            request: The wire request; its ids, name, scope, platform and brief are copied.
            held: The capability strings the requester actually holds (the Queen validated the
                request against them); the request's own `required_capabilities` when omitted.

        Returns:
            The brief; `requested_by` is the request's Warden.
        """
        return cls(
            tool_id=request.tool_id,
            requested_by=request.warden_id,
            name=request.name,
            description=request.description,
            required_capabilities=held if held is not None else request.required_capabilities,
            platform=request.platform,
            scope=request.scope,
            cell_id=request.cell_id if request.scope is ToolScope.CELL else None,
            reason=request.reason,
        )

    def with_retry(self, report: QuarantineReport, previous_tool_source: str) -> ToolRequestBrief:
        """Return the brief for the one retry: the same request plus the failing report.

        Args:
            report: The Comb's report on the first attempt.
            previous_tool_source: The first attempt's `tool.py`, cut to what the prompt shows.

        Returns:
            A copy carrying `previous_report` and the (bounded) previous source.
        """
        return self.model_copy(
            update={
                "previous_report": report,
                "previous_tool_source": previous_tool_source[:MAX_PREVIOUS_SOURCE_CHARS],
            }
        )


class ScaffoldReply(BaseModel):
    """The structured reply the SCAFFOLDER slot gives: a tool's tests, source and declarations.

    The schema `complete_structured` shows the model and validates its reply against. It crosses
    the model boundary, so every field is described and bounded and extras are refused: a reply
    that tries to name its own scope, Cell or requester is malformed, not obeyed. Tests come
    before source in field order on purpose, so a model that fills the object top to bottom
    writes the tests first.
    """

    model_config = _MODEL_CONFIG

    description: str = Field(
        min_length=MIN_DESCRIPTION_CHARS,
        max_length=MAX_DESCRIPTION_CHARS,
        description="What the tool does and when a worker should call it, for the worker's model.",
    )
    input_schema_json: Annotated[
        str, Field(max_length=MAX_INPUT_SCHEMA_CHARS), AfterValidator(check_json_object_text)
    ] = Field(
        description="JSON Schema text for the arguments: an object with additionalProperties false."
    )
    test_source: str = Field(
        min_length=1,
        max_length=MAX_SOURCE_CHARS,
        description="The complete test_tool.py: unittest tests, written before the source.",
    )
    tool_source: str = Field(
        min_length=1,
        max_length=MAX_SOURCE_CHARS,
        description="The complete tool.py defining `async def run(session, arguments)`.",
    )
    version: str = Field(
        default="1.0.0",
        max_length=MAX_VERSION_CHARS,
        pattern=VERSION_PATTERN,
        description="Semantic version of this tool, three numbers.",
    )
    required_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            default=(),
            max_length=MAX_REQUIRED_CAPABILITIES,
            description="Capability strings the tool acts under; never more than the brief allows.",
        )
    )
    notes: str = Field(
        default="", max_length=MAX_NOTES_CHARS, description="A remark about the work, optional."
    )


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    """A scaffolded tool, ready for the Quarantine Comb: spec, files and its named package."""

    spec: ToolSpec  # Validated against the requester's capabilities.
    files: Mapping[str, bytes]  # Exactly tool.py, test_tool.py and spec.json.
    package: bytes  # The deterministic archive of `files`.
    package_sha256: str  # The hash of `package`: the version's name everywhere.

    @property
    def package_size_bytes(self) -> int:
        """Return the size of the package bytes."""
        return len(self.package)
