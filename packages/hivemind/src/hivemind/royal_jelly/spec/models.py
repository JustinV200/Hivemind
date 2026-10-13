"""Define ToolSpec, the description of a requested tool, and its canonical hash.

A tool is something a bee asks the Royal Jelly Lab (the tool-authoring pipeline) to build because
no existing tool does the job. `ToolSpec` is that request made precise: the tool's name and
version, the model-facing description a Worker's model will read, the JSON Schema of its
arguments, the capabilities it needs, where its entry point lives, and who may see it once
promoted into the Comb Registry (the catalogue of promoted tools). It crosses a boundary (it is
written to `spec.json` inside a package and to the registry), so it is a frozen pydantic model.
`spec_sha256` is the one canonical hash of a spec; the Quarantine Comb's report records it and the
registry refuses a promotion whose report names a different one.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.spec. Built by
    the scaffolder and the request path, checked by `hivemind.royal_jelly.spec.validate`, packed
    by `.package`, proven by the Quarantine Comb and stored by `hivemind.royal_jelly.registry`.
    Calls into waggle's authoring bounds (never redeclared here) and nothing else.

Key invariants:
    - Every bound is waggle's own (`waggle.messages.tool.authoring`); the same name, version,
      description, schema and capability limits apply here as on the wire.
    - `cell_id` is set exactly when `scope` is CELL, and `supported_os` is non-empty and unique.
    - `spec_sha256` and `ToolSpec.to_json` use one canonical form (sorted keys, no whitespace),
      so a spec read back from `spec.json` hashes the same as the one that was written.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the fields.
    - waggle.messages.tool.authoring for ToolRequest/ToolPromoted, which carry the same bounds.
    - hivemind.royal_jelly.spec.validate for the refusals a well-formed spec can still earn.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from waggle.messages.base import CellIdField, ToolIdField, WardenIdField
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import (
    MAX_CAPABILITY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_INPUT_SCHEMA_CHARS,
    MAX_REQUIRED_CAPABILITIES,
    MAX_VERSION_CHARS,
    MIN_DESCRIPTION_CHARS,
    MIN_SUPPORTED_OS,
    TOOL_NAME_PATTERN,
    VERSION_PATTERN,
    ToolScope,
)
from waggle.messages.tool.json_text import check_json_object_text

DEFAULT_ENTRY_POINT = "tool:run"  # Module `tool`, attribute `run`: the one shape a package has.
ENTRY_POINT_PATTERN = r"^[a-z_][a-z0-9_]*:[a-z_][a-z0-9_]*$"  # `module:attribute`, no dots.
MAX_ENTRY_POINT_CHARS = 129  # Two 64-character identifiers and the colon.
MAX_DEPENDENCIES = 16  # A generated tool imports a handful of packages; more is a design smell.
MAX_DEPENDENCY_CHARS = 128  # One PyPI distribution name; real ones are far shorter.

__all__ = [
    "DEFAULT_ENTRY_POINT",
    "ENTRY_POINT_PATTERN",
    "MAX_DEPENDENCIES",
    "MAX_DEPENDENCY_CHARS",
    "MAX_ENTRY_POINT_CHARS",
    "ToolSpec",
    "spec_sha256",
]

# A JSON Schema document as text: bounded first, then proven to parse as a JSON object, using
# waggle's own check so a spec and its eventual ToolPromoted reject the same inputs.
_SchemaText = Annotated[
    str, Field(max_length=MAX_INPUT_SCHEMA_CHARS), AfterValidator(check_json_object_text)
]


class ToolSpec(BaseModel):
    """What a requested tool must be: name, arguments, needs, entry point and visibility.

    Crosses a boundary twice: as `spec.json` inside a tool package and as `spec_json` in the Comb
    Registry. A well-formed spec can still be refused by `validate_spec` (it asks for more than
    its requester holds, or its schema allows extra properties).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: ToolIdField = Field(description="Minted by the first requester; names the tool.")
    requested_by: WardenIdField = Field(
        description="The Warden (per-Cell supervisor) whose grant bounds the capabilities asked."
    )
    name: str = Field(
        pattern=TOOL_NAME_PATTERN,
        description="The tool's name; once promoted, the capability string tool:<name>.",
    )
    version: str = Field(
        max_length=MAX_VERSION_CHARS,
        pattern=VERSION_PATTERN,
        description="Semantic version text (three numbers); immutable once promoted.",
    )
    description: str = Field(
        min_length=MIN_DESCRIPTION_CHARS,
        max_length=MAX_DESCRIPTION_CHARS,
        description="Model-facing text a Worker's model reads to decide when to call the tool.",
    )
    input_schema_json: _SchemaText = Field(
        description="JSON Schema text for the tool's arguments; the top level must be an object "
        "with additionalProperties false (checked by validate_spec)."
    )
    required_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            max_length=MAX_REQUIRED_CAPABILITIES,
            description="Capability strings the tool acts under; none may exceed the requester's "
            "own set.",
        )
    )
    entry_point: str = Field(
        default=DEFAULT_ENTRY_POINT,
        max_length=MAX_ENTRY_POINT_CHARS,
        pattern=ENTRY_POINT_PATTERN,
        description="`module:attribute` of the async callable the loader runs (default tool:run).",
    )
    dependencies: tuple[
        Annotated[str, Field(min_length=1, max_length=MAX_DEPENDENCY_CHARS)], ...
    ] = Field(
        default=(),
        max_length=MAX_DEPENDENCIES,
        description="PyPI names imported beyond the standard library; the sandbox has no "
        "network, so a non-empty list fails the static gate in phase 9.",
    )
    supported_os: tuple[OsFamily, ...] = Field(
        min_length=MIN_SUPPORTED_OS,
        description="Platforms the tool is written and quarantined for; unique.",
    )
    scope: ToolScope = Field(description="HIVE (everywhere) or CELL (one Cell's bees only).")
    cell_id: CellIdField | None = Field(
        default=None, description="The one Cell a CELL-scope tool belongs to; None for HIVE."
    )

    @field_validator("supported_os")
    @classmethod
    def _supported_os_unique(cls, value: tuple[OsFamily, ...]) -> tuple[OsFamily, ...]:
        """Reject a platform listed twice."""
        # A repeated member is a requester bug; refusing it keeps the loader's set semantics.
        if len(set(value)) != len(value):
            raise ValueError("ToolSpec supported_os must be unique.")
        return value

    @model_validator(mode="after")
    def _cell_scope_names_its_cell(self) -> Self:
        """Require cell_id exactly when scope is CELL."""
        # A CELL-scope tool without a Cell is visible nowhere; a HIVE-scope one naming a Cell
        # would let a Cell-bound promotion masquerade as the Hive's. Checked in both directions.
        if (self.scope is ToolScope.CELL) != (self.cell_id is not None):
            raise ValueError(
                f"ToolSpec cell_id is required exactly when scope is CELL, got scope "
                f"{self.scope.value} with cell_id {self.cell_id}."
            )
        return self

    def to_json(self) -> str:
        """Render the spec in its canonical text form.

        Returns:
            Compact JSON with sorted keys: the text written to `spec.json` and hashed by
            `spec_sha256`, so the two can never disagree.
        """
        # mode="json" turns enums and tuples into plain JSON values; sorting the keys here (the
        # model's own dump keeps field order) makes the text independent of field declaration.
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse a spec from the text `to_json` (or any equivalent JSON) produced.

        Args:
            text: JSON text of a spec.

        Returns:
            The validated spec.

        Raises:
            pydantic.ValidationError: The text is not a well-formed spec.
        """
        return cls.model_validate_json(text)


def spec_sha256(spec: ToolSpec) -> str:
    """Return the canonical SHA-256 of a spec, as lowercase hex.

    Args:
        spec: The spec to hash.

    Returns:
        The digest of `spec.to_json()`; the Quarantine Comb's report records it and the Comb
        Registry compares it against the spec offered for promotion.
    """
    return hashlib.sha256(spec.to_json().encode("utf-8")).hexdigest()
