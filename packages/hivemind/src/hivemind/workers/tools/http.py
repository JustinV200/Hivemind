"""Implement http_request: a network tool the Guard and the Capping gate both authorise first.

`waggle.messages.capping.ActionKind` has no network shape yet (roadmap step 3.16's own note: "no
network shape yet"), so `http_request` proposes an `ACTION_SEQUENCE` with one step describing the
call on the `NETWORK_EGRESS` tier. Roadmap step 10.3 (ADR-0031) makes the tool work: the
destination's `net:<host>` is checked through the Guard's `Enforcer` at the `tool_invocation` point
before any proposal is built (a Worker without it is refused with the reason on the trail as
`guard.denied`, not a silent string), and `hivemind.supervision.capping.checks.deterministic.
SchemaCheck` now passes a well-formed network step on its own tier (whose ALLOWLIST rung checks
the same `net:<host>` again), whose apply is only the authorisation (`hivemind.supervision.
capping.apply`): once the gate reaches `VERIFIED`, `_send` makes the one request. `httpx` is
imported only inside `_send`, so nothing else here ever opens a socket.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `hivemind.workers.tools.registry.build_registry` for every Worker (a capability decides at
    invocation). Calls into `hivemind.guard`, `hivemind.llm`, `hivemind.supervision.capping`,
    `hivemind.workers.tools.authorize`, `hivemind.workers.tools.proposals`,
    `hivemind.workers.tools.registry`, waggle and, inside
    `_send` only, `httpx` (codingrules section 4: `httpx` may be imported here, and nowhere else
    under `workers/`, because this is the http tool's own client, not a model-server client).

Key invariants:
    - `_send` is called only when `outcome.state is ProposalState.VERIFIED`: nothing is sent that
      the Guard and the gate have not both passed.
    - `http_request` checks the host's `net` capability through the Enforcer before ever building
      a Proposal: a Worker without it is refused immediately, with the Guard's reason on the
      trail, rather than spending a whole propose/check/reject round trip.

See Also:
    - .claude/codingrules.md section 8.6 for "httpx itself is not banned elsewhere... what is
      banned outside the adapters is a model-server client."
    - .claude/roadmap.md phase 3 step 3.16 for the http.py bullet this module implements.
    - hivemind.workers.tools.proposals for make_proposal, cap and describe.
    - hivemind.supervision.capping.checks.deterministic for SchemaCheck and NetworkAllowlistCheck,
      the rungs a network step passes (a well-formed step, and `net:<host>` held) on its tier.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint, InvalidCapabilityError
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.supervision.capping import GateOutcome, ProposalState, RiskTier
from hivemind.workers.tools.authorize import authorize, refusal_text
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import ToolInvocation, ToolSpec
from hivemind.workers.tools.session import MAX_TOOL_RESULT_CHARS
from waggle.messages.capping import ActionKind, ProposedAction

HTTP_METHODS = ("GET", "POST")  # v0's supported methods; matches the tool's own schema enum.
_DEFAULT_TIMEOUT_S = 30.0  # Generous for a one-off request; a slower host is refused as failed.

HTTP_DEFINITION = ToolDefinition(
    name="http_request",
    description=(
        "Make an HTTP GET or POST request; requires a net capability for the URL's host, and "
        "passes the Capping gate on its network_egress tier before it is sent."
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
    """Check the host's `net`, propose the call to the Capping gate, and send it once VERIFIED.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `method` (`"GET"` or `"POST"`), `url` and an optional `body`.

    Returns:
        A readable string for malformed arguments or a refused `net` capability; the response's
        status and text once the gate reached VERIFIED; otherwise
        `hivemind.workers.tools.proposals.describe`'s rendering of the gate's outcome.
    """
    method = arguments.get("method")
    url = arguments.get("url")
    if not isinstance(method, str) or method not in HTTP_METHODS:
        return f"method must be one of {HTTP_METHODS!r}."
    if not isinstance(url, str) or not url:
        return "url must be a non-empty string."
    host = urlsplit(url).hostname or ""
    # A model wrote this URL, so its host is untrusted text: one that is not a host or an address
    # in the `net` grammar (roadmap step 10.1) is refused here, never raised out of the tool.
    try:
        needed = Capability.parse(f"{CapabilityFamily.NET.value}:{host}")
    except InvalidCapabilityError:
        return f"url {url!r} names no valid host; the request was never sent."
    # Roadmap step 10.3: the destination is a tool_invocation check, so a refusal is on the trail.
    decision = await authorize(invocation, EnforcementPoint.TOOL_INVOCATION, needed)
    if not decision.allowed:
        return refusal_text(decision)
    body = arguments.get("body")
    outcome = await _propose(invocation, method, url)
    if outcome.state is ProposalState.VERIFIED:
        # External await: one HTTP request, bounded by _DEFAULT_TIMEOUT_S inside _send.
        return await _send(method, url, body if isinstance(body, str) else None)
    return describe(outcome)


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
    return await cap(invocation, proposal)


async def _send(method: str, url: str, body: str | None) -> str:
    """Actually perform the HTTP call, once the Guard and the gate have both passed it.

    `httpx` is imported here, and nowhere else in this module, so it is only ever pulled in for a
    request that is really being sent -- and so `test_http.py` can prove nothing was sent for a
    refused call by asserting this function itself was never called.
    """
    import httpx  # SAFETY: the http tool's own client, not a model-server client (codingrules 8.6).

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            response = await client.request(method, url, content=body)
    except httpx.HTTPError as exc:
        # A refused connection or a timeout is the tool's result for the model to read, never an
        # exception that would end the Worker's whole attempt.
        return f"the request to {url!r} failed: {type(exc).__name__}."
    return f"{response.status_code}: {response.text[:MAX_TOOL_RESULT_CHARS]}"


HTTP_SPEC = ToolSpec(definition=HTTP_DEFINITION, run=http_request)
