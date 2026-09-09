"""Implement http_request: a NET-capability-gated network tool, always rejected by v0's gate.

`waggle.messages.capping.ActionKind` has no network shape yet (roadmap step 3.16's own note: "no
network shape yet"), so `http_request` proposes an `ACTION_SEQUENCE` with one step describing the
call and lets the Capping gate speak for itself: `hivemind.supervision.capping.checks.
deterministic.SchemaCheck` rejects every `ACTION_SEQUENCE` in v0 (it is not one of the two kinds
`hivemind.supervision.capping.apply.apply_action` can apply), so a `GateOutcome` here is always
`REJECTED` and never `VERIFIED`. `httpx` is imported only inside `_send`, the one branch a
`VERIFIED` outcome would reach and v0 never does, so this module never actually opens a socket. A
later minor bump to the `waggle` protocol that adds a real network `ActionKind` is what turns
`_send` from dead code into a live one.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `hivemind.workers.tools.registry.build_registry`, only when a Worker holds at least one `net`
    capability. Calls into `hivemind.guard`, `hivemind.llm`, `hivemind.supervision.capping`,
    `hivemind.workers.tools.proposals`, `hivemind.workers.tools.registry`, waggle and, inside
    `_send` only, `httpx` (codingrules section 4: `httpx` may be imported here, and nowhere else
    under `workers/`, because this is the http tool's own client, not a model-server client).

Key invariants:
    - `_send` is called only when `outcome.state is ProposalState.VERIFIED`; v0's gate can never
      reach that state for an `ACTION_SEQUENCE`, so no request is ever actually sent
      (`tests/unit/workers/tools/test_http.py` proves it by asserting `_send` is never called).
    - `http_request` checks a `net` capability itself, before ever building a Proposal: a Worker
      with no `net` capability at all is refused immediately, with a specific reason, rather than
      spending a whole propose/check/reject round trip to say the same thing.

See Also:
    - .claude/codingrules.md section 8.6 for "httpx itself is not banned elsewhere... what is
      banned outside the adapters is a model-server client."
    - .claude/roadmap.md phase 3 step 3.16 for the http.py bullet this module implements.
    - hivemind.workers.tools.proposals for make_proposal, cap and describe.
    - hivemind.supervision.capping.checks.deterministic for SchemaCheck, the rung that always
      rejects an ACTION_SEQUENCE in v0.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from hivemind.guard import Capability, CapabilityFamily
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.supervision.capping import GateOutcome, ProposalState, RiskTier
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import ToolInvocation, ToolSpec
from hivemind.workers.tools.session import MAX_TOOL_RESULT_CHARS
from waggle.messages.capping import ActionKind, ProposedAction

HTTP_METHODS = ("GET", "POST")  # v0's supported methods; matches the tool's own schema enum.
_DEFAULT_TIMEOUT_S = 30.0  # Generous for a one-off request; _send is unreachable in v0 regardless.

HTTP_DEFINITION = ToolDefinition(
    name="http_request",
    description=(
        "Make an HTTP GET or POST request; requires a net capability for the URL's host. Network "
        "egress is not cappable in v0, so every request is currently refused at the Capping gate."
    ),
    parameters={
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": list(HTTP_METHODS)},
            "url": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["method", "url"],
        "additionalProperties": False,
    },
)

__all__ = ["HTTP_DEFINITION", "HTTP_METHODS", "HTTP_SPEC", "http_request"]


async def http_request(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Propose an HTTP call and report the Capping gate's verdict; v0 always rejects it.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `method` (`"GET"` or `"POST"`), `url` and an optional `body`.

    Returns:
        A readable string for malformed arguments or a missing `net` capability; otherwise
        `hivemind.workers.tools.proposals.describe`'s rendering of the gate's REJECTED outcome,
        with a note that network egress is not cappable in v0.
    """
    method = arguments.get("method")
    url = arguments.get("url")
    if not isinstance(method, str) or method not in HTTP_METHODS:
        return f"method must be one of {HTTP_METHODS!r}."
    if not isinstance(url, str) or not url:
        return "url must be a non-empty string."
    ctx = invocation.ctx
    host = urlsplit(url).hostname or ""
    needed = Capability(family=CapabilityFamily.NET, scope=host)
    if not ctx.capabilities.allows(needed):
        return f"no net capability covers host {host!r}; the request was never sent."
    body = arguments.get("body")
    outcome = await _propose(invocation, method, url)
    if outcome.state is ProposalState.VERIFIED:
        # Unreachable in v0 (module docstring): SchemaCheck always rejects an ACTION_SEQUENCE.
        return await _send(method, url, body if isinstance(body, str) else None)
    return f"{describe(outcome)}; network egress is not cappable in v0."


async def _propose(invocation: ToolInvocation, method: str, url: str) -> GateOutcome:
    """Build and run the ACTION_SEQUENCE Proposal for one HTTP call; split for readability only."""
    action = ProposedAction(
        kind=ActionKind.ACTION_SEQUENCE,
        summary=f"{method} {url}"[:200],
        diff=None,
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(),
        steps=(f"{method} {url}",),
    )
    request = ProposalRequest(
        tier=RiskTier.NETWORK_EGRESS,
        action=action,
        postconditions=(),
        reason=f"Drone http_request {method} {url}",
    )
    proposal = make_proposal(invocation.ctx, invocation.assignment, request)
    return await cap(invocation.ctx, proposal)


async def _send(method: str, url: str, body: str | None) -> str:
    """Actually perform the HTTP call; unreachable in v0 (module docstring).

    `httpx` is imported here, and nowhere else in this module, so it is never pulled in for a
    branch v0 never takes -- and so `test_http.py` can prove nothing is sent by asserting this
    function itself is never called, without needing a network-mocking library.
    """
    import httpx  # SAFETY: the http tool's own client, not a model-server client (codingrules 8.6).

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
        response = await client.request(method, url, content=body)
        return f"{response.status_code}: {response.text[:MAX_TOOL_RESULT_CHARS]}"


HTTP_SPEC = ToolSpec(definition=HTTP_DEFINITION, run=http_request)
