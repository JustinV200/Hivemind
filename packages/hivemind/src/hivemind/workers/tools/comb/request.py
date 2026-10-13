"""Implement request_tool: ask the Hive to author a tool the Worker lacks, then wait for it.

The Royal Jelly Lab (the Hive's tool-authoring pipeline) is the only way a new tool enters the
Hive, and a Worker reaches it the way it reaches everything above it: over its own link to its
Warden. `request_tool(name, description, required_capabilities, scope)` builds a `ToolRequest`
(the tool id minted here, the Cell and platform from the Worker's own Cell, the Warden from the
channel), sends it through `ctx.tools` and waits, bounded, for the `tool.promoted` that fulfils it
or a `control.error` that refuses it. It needs the `tool:request` capability, checked through the
Guard at the tool-invocation point like every capability, and a Worker can only ask for
capabilities it holds itself (the Queen validates again). A promotion does not push anything into
the Worker: its Warden re-attenuates the Worker's bundle, and the bounded loop notices the bundle's
new version and offers the tool on its next round (`ToolsChangedError`).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.comb`. Registered by
    `build_registry` only when the runtime wired `ctx.tools`. Calls into `hivemind.cell` (the
    platform report), `hivemind.guard`, `hivemind.llm` (definitions) and waggle only.

Key invariants:
    - The Worker never invents the Warden's id: it comes from `ctx.tools.warden_id`, which the
      runtime fills from the link it actually has.
    - A request names no capability the Worker does not hold, and never a `tool:` capability.
    - The tool returns text either way; on success it says whether the tool is callable now,
      which is true only when the Warden has already put it in this Worker's bundle.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) and .claude/roadmap.md step 9.7.
    - hivemind.workers.runtime.tools for the channel that carries the request.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint, InvalidCapabilityError
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.workers.tools.authorize import authorize, refusal_text
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec
from waggle.ids import WardenId, new_tool_id
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import (
    MAX_DESCRIPTION_CHARS,
    TOOL_NAME_PATTERN,
    ToolPromoted,
    ToolRequest,
    ToolScope,
)

REQUEST_TOOL_DEFINITION = ToolDefinition(
    name="request_tool",
    description=(
        "Ask the Hive to build a tool you do not have, when no tool you are offered can do a step "
        "your task needs. Give it a snake_case name, say precisely what it must do, list the "
        "capabilities it will need (only ones you hold yourself), and choose its scope: 'cell' "
        "for a tool only this machine's workers need, 'hive' for one every machine may use. This "
        "waits while the tool is written and proved in a sandbox, which can take minutes; if it "
        "is promoted and handed to you, the result says so and you may call it by name."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "required_capabilities": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "enum": ["cell", "hive"]},
            "reason": {"type": "string"},
        },
        "required": ["name", "description", "required_capabilities", "scope"],
        "additionalProperties": False,
    },
)

_NAME_RE = re.compile(TOOL_NAME_PATTERN)  # The wire's own tool-name shape.
MAX_REASON_CHARS = 500  # A request's reason when derived from the task's objective.

__all__ = ["MAX_REASON_CHARS", "REQUEST_TOOL_DEFINITION", "REQUEST_TOOL_SPEC", "request_tool"]


async def request_tool(invocation: ToolInvocation, arguments: JsonObject) -> str | ToolOutput:
    """Send a `ToolRequest` up the chain and report how it ended.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `name`, `description`, `required_capabilities` and `scope` (required), and an
            optional `reason` (defaults to the task's objective).

    Returns:
        A readable line for a malformed or refused request, a refusal from the Hive, or a Guard
        denial of `tool:request`; otherwise a line saying the tool was promoted and whether it is
        in this Worker's bundle to call now.
    """
    ctx = invocation.ctx
    if ctx.tools is None:
        return ToolOutput(
            text="the Hive's tool authoring is not reachable from this Worker.", is_error=True
        )
    # Holding tool:request is its own capability, beside tool:request_tool that the registry
    # checked: a Worker may be allowed to use the tool and still not be allowed to ask for more.
    decision = await authorize(
        invocation,
        EnforcementPoint.TOOL_INVOCATION,
        Capability(family=CapabilityFamily.TOOL_REQUEST, scope=""),
    )
    if not decision.allowed:
        return ToolOutput(text=refusal_text(decision), is_error=True)
    built = _build_request(invocation, arguments, ctx.tools.warden_id)
    if isinstance(built, str):
        return ToolOutput(text=built, is_error=True)
    # External await: the Queen scaffolds, sandboxes and promotes the tool, minutes at most; the
    # channel bounds it and answers a timeout as an error message, so this never hangs.
    answer = await ctx.tools.request(built)
    if isinstance(answer, ErrorMessage):
        text = f"the Hive refused or could not build the tool ({answer.code}): {answer.message}"
        return ToolOutput(text=text, is_error=True)
    return _promoted_text(invocation, answer)


REQUEST_TOOL_SPEC = ToolSpec(definition=REQUEST_TOOL_DEFINITION, run=request_tool)


def _build_request(
    invocation: ToolInvocation, arguments: JsonObject, warden_id: WardenId
) -> ToolRequest | str:
    """Validate the arguments and build the wire request, or return the line to show the model."""
    ctx = invocation.ctx
    name, description = arguments.get("name"), arguments.get("description")
    if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
        return "name must be snake_case: a lowercase letter, then letters, digits or underscores."
    if not isinstance(description, str) or not 0 < len(description) <= MAX_DESCRIPTION_CHARS:
        return f"description must be 1 to {MAX_DESCRIPTION_CHARS} characters."
    scope = {"cell": ToolScope.CELL, "hive": ToolScope.HIVE}.get(str(arguments.get("scope")))
    if scope is None:
        return "scope must be 'cell' or 'hive'."
    capabilities = _capabilities(invocation, arguments.get("required_capabilities"))
    if isinstance(capabilities, str):
        return capabilities
    reason = arguments.get("reason")
    if not isinstance(reason, str) or not reason:
        reason = invocation.assignment.objective[:MAX_REASON_CHARS]
    platform, _flags = ctx.cell.capabilities.to_wire()
    try:
        return ToolRequest(
            tool_id=new_tool_id(ctx.clock),
            warden_id=warden_id,
            worker_id=ctx.worker_id,
            task_id=invocation.assignment.task_id,
            cell_id=ctx.cell.id,
            scope=scope,
            name=name,
            description=description,
            required_capabilities=capabilities,
            platform=platform,
            reason=reason[:MAX_REASON_CHARS],
        )
    except ValidationError as exc:
        return f"the request is not valid: {exc.errors()[0]['msg']}"


def _capabilities(invocation: ToolInvocation, raw: object) -> tuple[str, ...] | str:
    """Check the capability list: strings, parseable, held by this Worker, none a `tool:` one."""
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return "required_capabilities must be a list of capability strings."
    for text in raw:
        try:
            capability = Capability.parse(text)
        except InvalidCapabilityError:
            return f"required capability {text!r} is not a valid capability string."
        family = capability.family.value
        if family == "tool" or family.startswith("tool:"):
            return f"required capability {text!r} is a tool capability; tools never grant tools."
        if not invocation.ctx.capabilities.allows(capability):
            return f"you do not hold {text!r}, so you cannot ask for a tool that needs it."
    return tuple(str(item) for item in raw)


def _promoted_text(invocation: ToolInvocation, promoted: ToolPromoted) -> ToolOutput:
    """Say whether the promoted tool is callable now: only if the Warden put it in the bundle."""
    bundle = invocation.ctx.comb_tools
    handed = bundle is not None and any(
        entry.name == promoted.name and entry.version == promoted.version
        for entry in bundle.entries()
    )
    if handed:
        return ToolOutput(
            text=f"tool {promoted.name!r} {promoted.version} is now available; call it by name."
        )
    return ToolOutput(
        text=f"tool {promoted.name!r} {promoted.version} was promoted, but your Warden has not "
        "handed it to you yet; it is not callable in this attempt."
    )
