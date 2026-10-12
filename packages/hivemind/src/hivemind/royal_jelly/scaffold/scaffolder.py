"""Define the Scaffolder protocol and ModelScaffolder: a model writes a tool, the Hive checks it.

Roadmap 9.3. A Scaffolder turns a `ToolRequestBrief` into a `ScaffoldResult`: a validated
`ToolSpec`, the three files of a tool package and the package bytes. `ModelScaffolder` does it
with one structured call on `ModelSlot.SCAFFOLDER` (`hivemind.llm.complete_structured`, so the
degradation ladders apply): the model fills a `ScaffoldReply`, and everything it says is then
treated as untrusted. The spec's identity (name, scope, Cell, tool id, requester) and its platform
(`supported_os` is the target Cell's OS alone, `dependencies` empty) come from the brief; the
model's capabilities are proved a subset of the brief's own with `validate_spec`, which is the
check that the model did not widen the request. The model's source is only packed, never run:
the Quarantine Comb is the only place it executes.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.scaffold. Called
    by the promotion flow (`PromotionFlow`); calls into `hivemind.llm` (the structured ladder),
    the spec package and `hivemind.guard` (capability sets). The model call goes through the
    injected `CallGate`, so the Fanner meters it like any other.

Key invariants:
    - A reply is trusted for its text only: description, schema, tests, source, version and the
      capability strings it asks for; every other spec field comes from the brief.
    - `validate_spec` runs against the brief's capabilities, so a widened spec raises
      `SpecRejectedError` before anything is packed.
    - The returned `files` are exactly `tool.py`, `test_tool.py` and `spec.json`, and `spec.json`
      is the canonical text of the returned spec.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the spec rules.
    - hivemind.royal_jelly.scaffold.fake for FakeScaffolder, the test double.
    - hivemind.honey_store.ripening.summarise for the structured-call shape this follows.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from pydantic import ValidationError

from hivemind.common.logging import get_logger
from hivemind.guard import CapabilitySet
from hivemind.llm import BoundModel, CallGate, LLMError, complete_structured
from hivemind.royal_jelly.errors import SpecRejectedError
from hivemind.royal_jelly.scaffold.errors import ScaffoldFailedError
from hivemind.royal_jelly.scaffold.models import ScaffoldReply, ScaffoldResult, ToolRequestBrief
from hivemind.royal_jelly.scaffold.render import build_request
from hivemind.royal_jelly.spec import (
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    ToolSpec,
    pack,
    sha256_of,
    validate_spec,
)
from waggle.clock import Clock

SCAFFOLD_TIMEOUT_S = 600.0  # A local model writes a tool in minutes; a hung one never holds a job.

__all__ = ["SCAFFOLD_TIMEOUT_S", "ModelScaffolder", "Scaffolder"]

log = get_logger(__name__)


class Scaffolder(Protocol):
    """Turn a brief into a validated, packaged tool: the seam the promotion flow calls."""

    async def scaffold(self, brief: ToolRequestBrief) -> ScaffoldResult:
        """Produce the tool `brief` asks for.

        Args:
            brief: What to build; on the retry it carries the first attempt's failing report.

        Returns:
            The validated spec, its three files and the package that names the version by hash.

        Raises:
            SpecRejectedError: The spec the scaffold wrote asks for more than the brief allows, or
                is malformed.
            PackageError: The files cannot be packed (too large, an unsafe path).
            ScaffoldFailedError: No usable reply could be produced at all.
        """
        ...


class ModelScaffolder:
    """A `Scaffolder` backed by one structured call on the SCAFFOLDER model slot."""

    def __init__(self, bound: BoundModel, gate: CallGate, clock: Clock) -> None:
        """Wire the scaffolder to its model binding, call gate and clock.

        Args:
            bound: The SCAFFOLDER binding (and its fallback chain).
            gate: How each model call is made and metered (the Fanner's lane, or a direct gate).
            clock: Times the call for the log line; never read for any decision.
        """
        self._bound = bound
        self._gate = gate
        self._clock = clock

    async def scaffold(self, brief: ToolRequestBrief) -> ScaffoldResult:
        """Ask the model for a tool, then validate and pack what it wrote (see `Scaffolder`)."""
        request = build_request(self._bound, brief)
        started = self._clock.monotonic()
        try:
            # External await: one structured call, seconds to minutes on a local model; on timeout
            # the scaffold fails (the flow records `tool.rejected`) rather than hanging a job.
            async with asyncio.timeout(SCAFFOLD_TIMEOUT_S):
                result = await complete_structured(
                    self._bound, request, ScaffoldReply, gate=self._gate
                )
        except TimeoutError as exc:
            raise ScaffoldFailedError(brief.name, "timed out") from exc
        except LLMError as exc:
            raise ScaffoldFailedError(brief.name, type(exc).__name__) from exc
        scaffolded = assemble(brief, result.value)
        log.info(
            "royal_jelly.scaffolded",
            tool_id=brief.tool_id,
            name=brief.name,
            attempts=result.attempts,
            seconds=round(self._clock.monotonic() - started, 3),
        )
        return scaffolded


def assemble(brief: ToolRequestBrief, reply: ScaffoldReply) -> ScaffoldResult:
    """Validate a model's reply against its brief and pack the tool; the pure half of a scaffold.

    Args:
        brief: What was asked; the source of every identity field.
        reply: What the model wrote; untrusted until this returns.

    Returns:
        The validated spec, its files and its package.

    Raises:
        SpecRejectedError: The reply does not build a valid spec, or asks for more than the brief.
        PackageError: The files cannot be packed.
        ScaffoldFailedError: The model's text cannot be encoded as UTF-8.
    """
    spec = _spec_from(brief, reply)
    # The Queen validated the request against the requester already; here the same check proves
    # the model did not widen it: its capabilities must all be allowed by the brief's own set.
    validation = validate_spec(spec, CapabilitySet.parse(*brief.required_capabilities))
    if not validation.ok:
        raise SpecRejectedError(spec.name, validation.reasons)
    try:
        files = {
            TOOL_MODULE: reply.tool_source.encode("utf-8"),
            TESTS_MODULE: reply.test_source.encode("utf-8"),
            SPEC_FILE: spec.to_json().encode("utf-8"),
        }
    except UnicodeEncodeError as exc:
        # A lone surrogate in a JSON string is valid JSON but cannot be written to a file.
        raise ScaffoldFailedError(brief.name, "the source is not encodable as UTF-8") from exc
    package = pack(files)
    return ScaffoldResult(
        spec=spec, files=files, package=package, package_sha256=sha256_of(package)
    )


def _spec_from(brief: ToolRequestBrief, reply: ScaffoldReply) -> ToolSpec:
    """Build the spec: identity and platform from the brief, the declarations from the reply."""
    try:
        return ToolSpec(
            tool_id=brief.tool_id,
            requested_by=brief.requested_by,
            name=brief.name,
            version=reply.version,
            description=reply.description,
            input_schema_json=reply.input_schema_json,
            required_capabilities=reply.required_capabilities,
            dependencies=(),
            supported_os=(brief.platform.os,),
            scope=brief.scope,
            cell_id=brief.cell_id,
        )
    except ValidationError as exc:
        # The reply parsed but does not make a valid spec; every pydantic finding is a reason.
        reasons = tuple(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise SpecRejectedError(brief.name, reasons) from exc
