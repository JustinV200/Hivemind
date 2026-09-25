"""Implement http_request: a network tool the Guard and the Capping gate both authorise first.

`waggle.messages.capping.ActionKind` has no network shape yet (roadmap step 3.16's own note: "no
network shape yet"), so `http_request` proposes an `ACTION_SEQUENCE` with one step describing the
call on the `NETWORK_EGRESS` tier. Roadmap step 10.3 (ADR-0039) makes the tool work: the
destination's `net:<host>` is checked through the Guard's `Enforcer` at the `tool_invocation` point
before any proposal is built (a Worker without it is refused with the reason on the trail as
`guard.denied`, not a silent string), and `hivemind.supervision.capping.checks.deterministic.
SchemaCheck` passes a well-formed network step on its own tier (whose ALLOWLIST rung checks the
same `net:<host>` again), whose apply is only the authorisation (`hivemind.supervision.capping.
apply`): once the gate reaches `VERIFIED`, `_send` makes the one request.

Roadmap step 10.3a (ADR-0041, "bees never touch the Hive's own state") hardens the destination.
A capability string can spell a loopback host many ways the grammar never sees through (`127.1`,
`2130706433`, a DNS name answering 127.0.0.1), so once the name itself is held, the tool resolves
it (`WorkerContext.resolver`) and asks the Guard's floors about every address it got back: any
loopback, unspecified or link-local address, an IPv4-mapped form of one, or one of the Hive
Stand's own addresses is refused as `guard.denied` under `guard.state_floor.loopback`. The name is
checked before it is resolved, so a Worker never makes a lookup for a host it does not hold (a
lookup is itself a message to whoever answers for that name). WHY pinning: letting the HTTP client
resolve the name again would let a DNS answer that changes in between (rebinding) aim the request
somewhere unchecked, so `_send` connects to the one address that was checked, with the name kept
in the `Host` header and in TLS's SNI (`httpx`'s `sni_hostname` extension), exactly as
`hivemind.entrance.push.destinations` already does for webhooks. The URL is parsed once, with the
HTTP client's own parser, so the host the Guard judged is the host the request is built from.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `hivemind.workers.tools.registry.build_registry` for every Worker (a capability decides at
    invocation). Calls into `hivemind.guard` (and its `net` resolver seam), `hivemind.llm`,
    `hivemind.supervision.capping`, `hivemind.workers.tools.authorize`,
    `hivemind.workers.tools.proposals`, `hivemind.workers.tools.registry`, waggle and `httpx`
    (codingrules section 4: `httpx` may be imported here, and nowhere else under `workers/`,
    because this is the http tool's own client, not a model-server client).

Key invariants:
    - `_send` is called only when `outcome.state is ProposalState.VERIFIED`: nothing is sent that
      the Guard and the gate have not both passed.
    - Nothing is resolved for a host whose `net` capability the Worker does not hold, and nothing
      is sent unless every address the host resolved to passed the Guard's floors.
    - `_send` connects to exactly the address that was checked, never to a fresh lookup, and
      follows no redirect (httpx's default), so no response can steer it elsewhere.

See Also:
    - .claude/codingrules.md section 8.6 for "httpx itself is not banned elsewhere... what is
      banned outside the adapters is a model-server client."
    - .claude/roadmap.md phase 3 step 3.16 for the http.py bullet this module implements.
    - hivemind.guard.net for the resolver seam and the address predicates the floors apply.
    - hivemind.workers.tools.proposals for make_proposal, cap and describe.
    - hivemind.supervision.capping.checks.deterministic for SchemaCheck and NetworkAllowlistCheck,
      the rungs a network step passes (a well-formed step, and `net:<host>` held) on its tier.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint, InvalidCapabilityError
from hivemind.guard.net import (
    IPAddress,
    UnresolvableHostError,
    is_loopback_name,
    normalise_host,
    resolve_host,
)
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.supervision.capping import GateOutcome, ProposalState, RiskTier
from hivemind.workers.tools.authorize import authorize, floor_refusal_text, refusal_text
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import ToolInvocation, ToolSpec
from hivemind.workers.tools.session import MAX_TOOL_RESULT_CHARS
from waggle.messages.capping import ActionKind, ProposedAction

HTTP_METHODS = ("GET", "POST")  # v0's supported methods; matches the tool's own schema enum.
_DEFAULT_TIMEOUT_S = 30.0  # Generous for a one-off request; a slower host is refused as failed.
_DEFAULT_PORTS = {"http": 80, "https": 443}  # The only schemes the tool sends, and their ports.

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

__all__ = ["HTTP_DEFINITION", "HTTP_METHODS", "HTTP_SPEC", "PinnedRequest", "http_request"]


@dataclass(frozen=True, slots=True)
class PinnedRequest:
    """One request, pinned to the address the Guard checked, with its name kept for the receiver.

    Attributes:
        url: The URL as the model wrote it, parsed by the HTTP client's own parser.
        host: The URL's host, normalised (lowercase, no trailing dot, no IPv6 zone id).
        address: The checked address the request connects to.
    """

    url: httpx.URL
    host: str
    address: IPAddress

    def request_url(self) -> httpx.URL:
        """Return the URL to connect to: the checked address in place of the host.

        Returns:
            The same scheme, credentials, port, path and query, with the host replaced.
        """
        return self.url.copy_with(host=str(self.address))

    def headers(self) -> dict[str, str]:
        """Return the headers that keep the name the receiver expects on a pinned request.

        Returns:
            `Host` with the original host and port, and `Connection: close`, so a pooled
            connection to this address never carries a request for a different name.
        """
        return {"Host": self.url.netloc.decode("ascii"), "Connection": "close"}

    def extensions(self) -> dict[str, str]:
        """Return the httpx request extensions for a pinned request.

        Returns:
            `sni_hostname` for https, so TLS sends the name and verifies the certificate against
            it rather than against the address; nothing for plain http.
        """
        return {"sni_hostname": self.host} if self.url.scheme == "https" else {}


async def http_request(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Check the host, resolve and check its addresses, propose, and send once VERIFIED.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `method` (`"GET"` or `"POST"`), `url` and an optional `body`.

    Returns:
        A readable string for malformed arguments, a refused capability or floor, or a host that
        did not resolve; the response's status and text once the gate reached VERIFIED;
        otherwise `hivemind.workers.tools.proposals.describe`'s rendering of the gate's outcome.
    """
    method = arguments.get("method")
    url = arguments.get("url")
    if not isinstance(method, str) or method not in HTTP_METHODS:
        return f"method must be one of {HTTP_METHODS!r}."
    if not isinstance(url, str) or not url:
        return "url must be a non-empty string."
    # Roadmap step 10.3: the tool_invocation point, named here where the tool is called.
    pinned_or_refusal = await _authorize_destination(
        invocation, EnforcementPoint.TOOL_INVOCATION, url
    )
    if isinstance(pinned_or_refusal, str):
        return pinned_or_refusal
    body = arguments.get("body")
    outcome = await _propose(invocation, method, url)
    if outcome.state is ProposalState.VERIFIED:
        # External await: one HTTP request, bounded by _DEFAULT_TIMEOUT_S inside _send.
        return await _send(method, pinned_or_refusal, body if isinstance(body, str) else None)
    return describe(outcome)


async def _authorize_destination(
    invocation: ToolInvocation, point: EnforcementPoint, url: str
) -> PinnedRequest | str:
    """Check the URL's host, then every address it resolves to; return the pin or a refusal."""
    parsed = _parse(url)
    needed = _net_need(parsed[1]) if parsed is not None else None
    # A model wrote this URL: one with no host the `net` grammar can hold is refused, not raised.
    if parsed is None or needed is None:
        return f"url {url!r} names no valid host; the request was never sent."
    # The name is checked first, so nothing the Worker does not hold is ever resolved.
    decision = await authorize(invocation, point, needed)
    if not decision.allowed:
        return refusal_text(decision)
    addresses = await _addresses(invocation, parsed)
    if isinstance(addresses, str):
        return addresses
    # Roadmap step 10.3a: every address a connection could use meets the Hive-state floor.
    refused = await floor_refusal_text(invocation, point, needed, addresses)
    if refused is not None:
        return refused
    return PinnedRequest(url=parsed[0], host=parsed[1], address=addresses[0])


async def _addresses(
    invocation: ToolInvocation, parsed: tuple[httpx.URL, str]
) -> tuple[IPAddress, ...] | str:
    """Resolve the URL's host through the Worker's resolver; a readable string when it fails."""
    url, host = parsed
    # A localhost name is loopback by definition (RFC 6761); the floor refused it by name already.
    if is_loopback_name(host):
        return f"{host!r} is a loopback name; the request was never sent."
    port = url.port if url.port is not None else _DEFAULT_PORTS[url.scheme]
    try:
        # External await: one DNS lookup, bounded by hivemind.guard.net.RESOLVE_TIMEOUT_S.
        return await resolve_host(host, port, invocation.ctx.resolver)
    except UnresolvableHostError:
        return f"the host of {url!r} did not resolve; the request was never sent."


def _net_need(host: str) -> Capability | None:
    """Return `net:<host>`, or None when the host is not one the `net` grammar can hold.

    The host is untrusted text a model wrote (roadmap step 10.1: a scope is a host, a domain
    pattern, an address or a network), so a malformed one is an answer here, never an exception.
    """
    try:
        return Capability.parse(f"{CapabilityFamily.NET.value}:{host}")
    except InvalidCapabilityError:
        return None


def _parse(url: str) -> tuple[httpx.URL, str] | None:
    """Parse `url` with the HTTP client's own parser; None unless it is http(s) with a host.

    The client's parser, not a second one: two parsers that disagree about the host are a
    classic way to check one host and connect to another.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    if parsed.scheme not in _DEFAULT_PORTS or not parsed.raw_host:
        return None
    return parsed, normalise_host(parsed.raw_host.decode("ascii"))


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


async def _send(method: str, pinned: PinnedRequest, body: str | None) -> str:
    """Actually perform the HTTP call, pinned, once the Guard and the gate have both passed it.

    The only place in this module that opens a client, so `test_http.py` can prove nothing was
    sent for a refused call by asserting this function itself was never called.
    """
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            response = await client.request(
                method,
                pinned.request_url(),
                content=body,
                headers=pinned.headers(),
                extensions=pinned.extensions(),
            )
    except httpx.HTTPError as exc:
        # A refused connection or a timeout is the tool's result for the model to read, never an
        # exception that would end the Worker's whole attempt.
        return f"the request to {str(pinned.url)!r} failed: {type(exc).__name__}."
    return f"{response.status_code}: {response.text[:MAX_TOOL_RESULT_CHARS]}"


HTTP_SPEC = ToolSpec(definition=HTTP_DEFINITION, run=http_request)
