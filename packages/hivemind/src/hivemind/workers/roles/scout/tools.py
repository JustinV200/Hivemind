"""Build the Scout's own narrow tool registry, and report_findings: the tool that ends its loop.

A Scout (roadmap step 6.10) is read-only by design: `scout_build_registry` never offers
`run_command`, `write_file`, `keep`, `ask`, or any of the Exoskeleton's action tools (`click`,
`type`, `browser_fill`, `say`, ...). It offers `read_file` (session file reads), one GET-only
`http_request` (never POST, whatever a model asks for -- defence in depth beyond the tool's own
schema), and, only when the attached peripherals and the bound model can use them, the
Exoskeleton's structural and vision reads (`browser_navigate`, `browser_snapshot`, `browser_read`,
`browser_screenshot`, `see`) -- the same gating `hivemind.workers.tools.exoskeleton.offer.
exoskeleton_specs` uses. `report_findings` is the one write this role ever makes: it validates its
arguments as a `waggle.messages.task.ScoutReport`, writes it as JSON to `SCOUT_REPORT_FILE`
through the exact same capped path `hivemind.workers.tools.session.write_file` uses (so the
Warden's own FILE_EXISTS acceptance can see it), and, once that write verifies, raises
`hivemind.workers.roles.bounded_loop.executor.LoopStoppedError` with a claimed outcome carrying
the report -- ending the loop there rather than waiting for the model to also stop calling tools.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.scout`. `scout_build_registry`
    is `hivemind.workers.roles.scout.role.Scout`'s own `RoleProfile.build_tools`; `report_findings`
    is offered inside the registry it builds. Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.guard` (CapabilityFamily), `hivemind.llm`, `hivemind.workers.base` (WorkerOutcome),
    `hivemind.workers.context`, `hivemind.workers.roles.bounded_loop.executor` (LoopStoppedError),
    `hivemind.workers.tools`, `hivemind.workers.tools.exoskeleton` and waggle only.

Key invariants:
    - No tool this module offers proposes a write of any kind except `report_findings`'s own,
      which goes through the same capped path every other write does.
    - A tool this registry does not include is refused the ordinary way: `hivemind.workers.tools.
      registry.ToolRegistry.execute` reports "no tool named ... is offered" for anything not in
      its own `_specs`, with no special casing needed here.
    - `report_findings` only ever raises `LoopStoppedError` after its own write reports
      `state=VERIFIED`; a rejected or rolled-back write is returned as ordinary tool-result text,
      so the model can see why and, budget permitting, try again.

See Also:
    - .claude/roadmap.md step 6.10 for the Scout's own bullet.
    - waggle.messages.task.recon for ScoutReport and SCOUT_REPORT_FILE.
    - hivemind.workers.tools.session for write_file, the capped path this module reuses.
    - hivemind.workers.tools.exoskeleton.offer for exoskeleton_specs, the gating pattern this
      module mirrors for its own, narrower set of Exoskeleton tools.
    - hivemind.workers.roles.scout.role for Scout, this module's one caller.
"""

from __future__ import annotations

from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.guard import CapabilityFamily
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.bounded_loop.executor import LoopStoppedError
from hivemind.workers.tools import (
    READ_FILE_SPEC,
    ToolInvocation,
    ToolOutput,
    ToolRegistry,
    ToolSpec,
    http_request,
    write_file,
)
from hivemind.workers.tools.exoskeleton import (
    BROWSER_NAVIGATE_SPEC,
    BROWSER_READ_SPEC,
    BROWSER_SCREENSHOT_SPEC,
    BROWSER_SNAPSHOT_SPEC,
    SEE_SPEC,
)
from waggle.messages.task import SCOUT_REPORT_FILE, ScoutReport

_VERIFIED_PREFIX = "state=VERIFIED"  # hivemind.workers.tools.proposals.describe's own token.

REPORT_FINDINGS_DEFINITION = ToolDefinition(
    name="report_findings",
    description=(
        "File your reconnaissance report and end your task. Once this succeeds you are done; "
        "call it exactly once, with your best assessment so far, whether or not you are certain."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feasible": {
                "type": "boolean",
                "description": "Whether to commit Foragers to the work as planned.",
            },
            "summary": {"type": "string", "description": "One paragraph: what you looked at."},
            "findings": {"type": "array", "items": {"type": "string"}},
            "suggested_steps": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "targets": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["feasible", "summary"],
        "additionalProperties": False,
    },
)
SCOUT_HTTP_DEFINITION = ToolDefinition(
    name="http_request",
    description="Make an HTTP GET request; requires a net capability for the URL's host.",
    parameters={
        "type": "object",
        "properties": {"method": {"type": "string", "enum": ["GET"]}, "url": {"type": "string"}},
        "required": ["method", "url"],
        "additionalProperties": False,
    },
)

__all__ = [
    "REPORT_FINDINGS_DEFINITION",
    "SCOUT_HTTP_DEFINITION",
    "report_findings",
    "scout_build_registry",
    "scout_http_get",
]


async def report_findings(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput | str:
    """Validate `arguments` as a ScoutReport, write it, and end the loop once that verifies.

    Args:
        invocation: This attempt's context and assignment.
        arguments: The call's arguments, shaped like `ScoutReport`'s own fields.

    Returns:
        A readable string when the report fails validation, or when the write itself is rejected
        or rolled back (the model can see why and try again within its round budget).

    Raises:
        hivemind.workers.roles.bounded_loop.executor.LoopStoppedError: The report validated and
            its write verified; carries the claimed outcome the loop should end with.
    """
    try:
        report = ScoutReport.model_validate(arguments)
    except ValidationError as exc:
        return f"invalid report: {_first_error(exc)}"
    outcome_text = await write_file(
        invocation, {"path": SCOUT_REPORT_FILE, "content": report.model_dump_json()}
    )
    if not outcome_text.startswith(_VERIFIED_PREFIX):
        return outcome_text  # The gate rejected or rolled back the write; nothing to end on yet.
    clearance = HoneyClearance.from_wire(invocation.assignment.clearance)
    raise LoopStoppedError(
        WorkerOutcome(
            summary=report.summary,
            clearance=clearance,
            artifacts=(),
            claimed=True,
            handoff=None,
            spend_usd=0.0,  # Overlaid with this attempt's own telemetry spend by the runner.
            scout_report=report,
        )
    )


async def scout_http_get(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Run `http_request`, refusing anything but GET even if a model asks for it anyway.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `method` (must be `"GET"`) and `url`.

    Returns:
        `hivemind.workers.tools.http.http_request`'s own result for a GET call; a plain refusal
        string for anything else, without ever building a Proposal for it.
    """
    if arguments.get("method") != "GET":
        return "only GET is allowed for this role; the request was refused."
    return await http_request(invocation, arguments)


REPORT_FINDINGS_SPEC = ToolSpec(definition=REPORT_FINDINGS_DEFINITION, run=report_findings)
_SCOUT_HTTP_SPEC = ToolSpec(definition=SCOUT_HTTP_DEFINITION, run=scout_http_get)


def scout_build_registry(ctx: WorkerContext) -> ToolRegistry:
    """Build the narrow ToolRegistry a Scout attempt offers (module docstring).

    Args:
        ctx: This attempt's WorkerContext; `capabilities` decides the http tool, and
            `exoskeleton` and `bound` decide the browser and vision reads.

    Returns:
        A ToolRegistry with `read_file` and `report_findings` always, a GET-only `http_request`
        only when `ctx.capabilities` holds at least one `net` capability, and, when a browser or
        display is attached, exactly the reads `hivemind.workers.tools.exoskeleton.offer.
        exoskeleton_specs` would offer a Drone for the same peripherals -- never an action tool.
    """
    specs: list[ToolSpec] = [READ_FILE_SPEC, REPORT_FINDINGS_SPEC]
    if any(capability.family is CapabilityFamily.NET for capability in ctx.capabilities):
        specs.append(_SCOUT_HTTP_SPEC)
    specs.extend(_exoskeleton_read_specs(ctx))
    return ToolRegistry(specs)


def _exoskeleton_read_specs(ctx: WorkerContext) -> tuple[ToolSpec, ...]:
    """Return the read-only Exoskeleton tools this attempt's peripherals and model can use."""
    handle = ctx.exoskeleton
    if handle is None:
        return ()  # No Exoskeleton attached: nothing to see or browse.
    attached = handle.peripherals
    sees = ctx.bound.sees
    rows: tuple[tuple[bool, tuple[ToolSpec, ...]], ...] = (
        (
            attached.browser is not None,
            (BROWSER_NAVIGATE_SPEC, BROWSER_SNAPSHOT_SPEC, BROWSER_READ_SPEC),
        ),
        (attached.browser is not None and sees, (BROWSER_SCREENSHOT_SPEC,)),
        (attached.compound_eye is not None and sees, (SEE_SPEC,)),
    )
    return tuple(spec for offered, specs in rows if offered for spec in specs)


def _first_error(exc: ValidationError) -> str:
    """Render the first pydantic validation error as one short, readable line."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "report"
    return f"{location}: {error['msg']}"
