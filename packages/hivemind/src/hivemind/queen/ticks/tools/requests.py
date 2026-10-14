"""Take a Warden's `tool.request`: validate it at the door, then run the promotion flow as a job.

A Worker that lacks a capability asks its Warden, and the Warden relays a `tool.request` up; a
Warden may also ask for itself. The Queen is the only party that scaffolds and promotes a hive-scope
tool. This module is the door: the sender must be an attached Warden and the request must name it
(first-hop identity), the request must be for a hive-scope tool on that Warden's own Cell, and
every capability it asks the tool to need must be held by the requester: for a Worker, the task's
goal ceiling narrowed by the Worker role's default set; for a Warden, its own set. A passing
request starts one job in the tool lane: `PromotionFlow` scaffolds with a model, proves the package
in a sandbox, and promotes through the registry as the Queen. The job ends by answering the
requesting Warden, correlated to the request: `tool.promoted` on success, or `control.error`
(`hive.tool.rejected`, or `hive.tool.sandbox_unavailable` when no sandbox could be reached). A
refusal at the door is `control.error hive.tool.request_refused`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by the tools dispatch; calls into `hivemind.royal_jelly` (the flow, the scaffolder, the Comb's
    path decision), `hivemind.queen.authority` (the Warden's held set), `.jobs`, `.runner`,
    `.announce`, `.honey`, `.review` and `.replies`.

Key invariants:
    - The tick never waits: it validates, starts a job and returns; the job answers the Warden.
    - A tool's capability ceiling is the requester's, never the request's own claim.
    - One job per tool id: a second request for a tool in flight is refused, not queued.
    - Nothing here awaits a model in the tick; the scaffold call is in the job.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for the request, scopes and principals.
    - hivemind.royal_jelly.promotion.flow for the flow the job runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from hivemind.brood_chamber import TaskNotFoundError
from hivemind.cell import hive_stand_cell_id
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.guard import (
    Capability,
    CapabilitySet,
    GuardPolicyError,
    InvalidCapabilityError,
    role_set,
    worker_role_name,
)
from hivemind.queen.authority import goal_held, warden_held
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.tools import replies
from hivemind.queen.ticks.tools.announce import QueenAnnouncer
from hivemind.queen.ticks.tools.events import tool_identity
from hivemind.queen.ticks.tools.honey import QueenHoneyDeposit
from hivemind.queen.ticks.tools.jobs import start_job
from hivemind.queen.ticks.tools.review import QueenReviewer
from hivemind.queen.ticks.tools.runner import QueenSandboxRunner
from hivemind.royal_jelly import RoyalJellyAccess
from hivemind.royal_jelly.promotion import PromotionFlow, PromotionOutcome, PromotionPorts
from hivemind.royal_jelly.quarantine_comb import decide_path
from hivemind.royal_jelly.registry import QueenPrincipal
from hivemind.royal_jelly.scaffold import (
    ModelScaffolder,
    Scaffolder,
    ScaffoldFailedError,
    ScaffoldResult,
    ToolRequestBrief,
)
from hivemind.royal_jelly.tool_state import ToolState
from hivemind.supervision.attendant import InboxItem
from waggle.errors import WaggleError
from waggle.ids import MessageId, WardenId
from waggle.messages.tool import ToolRequest, ToolScope
from waggle.messages.tool.authoring import MAX_REQUIRED_CAPABILITIES

log = get_logger(__name__)

__all__ = ["build_ports", "handle_tool_request"]


@dataclass(frozen=True, slots=True)
class _Refusal:
    """Why a request was refused at the door: a stable code and one sentence."""

    code: str
    text: str


class _NoScaffolder:
    """The scaffolder of a Hive with no model on the SCAFFOLDER slot: it can only say so."""

    async def scaffold(self, brief: ToolRequestBrief) -> ScaffoldResult:
        """Fail the scaffold; the flow records it as a rejection at the `scaffold` stage."""
        raise ScaffoldFailedError(brief.name, "no model is bound to the SCAFFOLDER slot")


async def handle_tool_request(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Validate a `tool.request` and start its promotion job; report whether it was one.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id (live: the job reads it later).
        item: The ordered item; `item.principal` is the sending Warden, `item.id` the envelope
            id the answer is correlated to.

    Returns:
        True when `item.payload` was a `ToolRequest` (answered, or its job started); False
        otherwise, so the caller tries the next handler.
    """
    request = item.payload
    if not isinstance(request, ToolRequest):
        return False
    link = wardens.get(WardenId(item.principal))
    if link is None:
        log.debug("queen.tools.no_link", principal=item.principal)
        return True  # The Warden detached since: nobody to answer.
    admitted = await _admit(deps, link, request)
    brief = _brief(request, admitted)
    if isinstance(brief, _Refusal):
        await _deny(deps, link, item, brief)
    elif not start_job(deps, request.tool_id, _run(deps, wardens, link, (item, brief))):
        # `start_job` closed the unstarted coroutine; the requester is told, not queued.
        text = f"Tool {request.tool_id} is already in flight, or the Queen's tool lane is full."
        await _deny(deps, link, item, _Refusal(replies.REQUEST_REFUSED, text))
    return True


async def _deny(deps: QueenDeps, link: WardenLink, item: InboxItem, refusal: _Refusal) -> None:
    """Answer a request refused at the door with `control.error`, correlated to it."""
    await replies.refuse(deps, link, (item.id, item.payload_kind), refusal.code, refusal.text)


def _brief(request: ToolRequest, admitted: CapabilitySet | _Refusal) -> ToolRequestBrief | _Refusal:
    """Build the scaffolder's brief from an admitted request, or refuse an unusable one."""
    if isinstance(admitted, _Refusal):
        return admitted
    try:
        return ToolRequestBrief.from_request(request, _ceiling(admitted, request))
    except ValidationError as exc:
        count = exc.error_count()
        text = f"The request for tool {request.name} is not a usable brief ({count} errors)."
        return _Refusal(replies.REQUEST_REFUSED, text)


def _ceiling(held: CapabilitySet, request: ToolRequest) -> tuple[str, ...]:
    """Return the capability strings the tool may declare: the requester's own, bounded."""
    strings = held.as_strings()
    # A set wider than the wire's bound cannot be shown to the scaffolder whole; the request's own
    # capabilities (each already checked against `held`) are then the most the tool may declare.
    return strings if len(strings) <= MAX_REQUIRED_CAPABILITIES else request.required_capabilities


async def _admit(
    deps: QueenDeps, link: WardenLink, request: ToolRequest
) -> CapabilitySet | _Refusal:
    """Check the request at the door; return the requester's held set, or why it is refused."""
    if deps.royal_jelly is None:
        return _Refusal(replies.SANDBOX_UNAVAILABLE, "No Royal Jelly Lab is wired to this Hive.")
    if request.warden_id != link.warden_id:
        return _Refusal(
            replies.REQUEST_REFUSED, "A tool request must name the Warden that sent it."
        )
    if request.scope is not ToolScope.HIVE or request.cell_id != link.cell.id:
        return _Refusal(
            replies.REQUEST_REFUSED,
            "The Queen builds hive-scope tools on the requester's own Cell; a cell-scope tool is "
            "built and promoted by its own Warden.",
        )
    held = await _held(deps, link, request)
    if isinstance(held, _Refusal):
        return held
    for text in request.required_capabilities:
        try:
            allowed = held.allows(Capability.parse(text))
        except InvalidCapabilityError:
            return _Refusal(
                replies.REQUEST_REFUSED, f"Required capability {text!r} does not parse."
            )
        if not allowed:
            return _Refusal(
                replies.REQUEST_REFUSED, f"The requester does not hold the capability {text!r}."
            )
    return held


async def _held(
    deps: QueenDeps, link: WardenLink, request: ToolRequest
) -> CapabilitySet | _Refusal:
    """Return what the requester holds: a Warden's own set, or a Worker's narrowed slice."""
    if request.worker_id is None:
        return warden_held(deps, link.cell)
    if request.task_id is None:
        return _Refusal(replies.REQUEST_REFUSED, "A Worker's tool request must name its task.")
    try:
        task = await deps.chamber.get(request.task_id)
    except TaskNotFoundError:
        return _Refusal(
            replies.REQUEST_REFUSED, f"Task {request.task_id} is not known to the Queen."
        )
    if task.cell_id != link.cell.id:
        return _Refusal(
            replies.REQUEST_REFUSED, f"Task {task.id} does not run on the relaying Warden's Cell."
        )
    try:
        role = role_set(deps.enforcer.policy, worker_role_name(task.spec.role), deps.scratch_root)
    except GuardPolicyError:
        return _Refusal(
            replies.REQUEST_REFUSED, f"The Guard policy defines no role for task {task.id}."
        )
    goal = goal_held(task)
    if goal is None:
        return role  # The operator's own submission carries no ceiling: the role default stands.
    return CapabilitySet(capabilities=frozenset(c for c in role if goal.allows(c)))


def build_ports(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], access: RoyalJellyAccess
) -> PromotionPorts:
    """Build the ports a promotion flow runs over: the model scaffolder, the Queen's own effects.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden attached (live), for the announcer.
        access: The Lab's handles.

    Returns:
        The ports, with the Comb's own path decision for `path_for` (a non-Ubuntu target takes the
        real-Cell route) and the Queen's reviewer for a real-Cell proof at hive scope.
    """
    bound = access.scaffolder_bound
    scaffolder: Scaffolder = (
        ModelScaffolder(bound, deps.call_gate, deps.clock) if bound is not None else _NoScaffolder()
    )
    return PromotionPorts(
        scaffolder=scaffolder,
        runner=QueenSandboxRunner(deps),
        registry=access.registry,
        honey=QueenHoneyDeposit(deps),
        announcer=QueenAnnouncer(deps, wardens),
        path_for=lambda platform: decide_path(
            platform, access.config.sandbox_distribution, access.sandbox_os
        ),
        reviewer=QueenReviewer(deps),
    )


async def _run(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    link: WardenLink,
    request: tuple[InboxItem, ToolRequestBrief],
) -> None:
    """The job: run the flow, then answer the requesting Warden whatever the outcome."""
    item, brief = request
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - `_admit` refused a Hive with no Lab.
    flow = PromotionFlow(
        build_ports(deps, wardens, access),
        deps.trail,
        tool_identity(deps),
        deps.clock,
        max_retries=access.config.max_retries,
    )
    refused = (item.id, item.payload_kind)
    try:
        # External await: a model scaffolds, a sandbox proves, a registry promotes; the flow
        # bounds each step, and a cancellation (the Queen stopping) ends it here.
        outcome = await flow.run(brief, QueenPrincipal())
    except (HiveMindError, WaggleError, OSError) as exc:
        log.error("queen.tools.flow_failed", tool_id=brief.tool_id, error=repr(exc))
        failure = type(exc).__name__
        text = f"Tool {brief.name} could not be built: the Royal Jelly Lab failed ({failure})."
        await replies.refuse(deps, link, refused, replies.REJECTED, text)
        return
    await _answer(deps, link, refused, outcome)


async def _answer(
    deps: QueenDeps, link: WardenLink, refused: tuple[str, str], outcome: PromotionOutcome
) -> None:
    """Send the requester `tool.promoted` or a `control.error` that says why not."""
    if outcome.state is ToolState.PROMOTED and outcome.change is not None:
        stand = hive_stand_cell_id(deps.identity.node_id)
        assert outcome.report is not None  # noqa: S101 - a promotion always has its proof.
        message = replies.promoted_message(outcome.change.row, outcome.report, stand)
        await replies.reply(deps, link, message, MessageId(refused[0]))
        return
    name = outcome.spec.name if outcome.spec is not None else "the requested tool"
    detail = _failing_reason(outcome)
    text = f"Tool {name} was rejected ({outcome.reason}){': ' + detail if detail else ''}."
    code = (
        replies.SANDBOX_UNAVAILABLE
        if outcome.reason.endswith("sandbox_unavailable")
        else replies.REJECTED
    )
    await replies.refuse(deps, link, refused, code, text)


def _failing_reason(outcome: PromotionOutcome) -> str:
    """Return the failing gate's one-sentence reason from the last report, or an empty string."""
    report = outcome.report
    if report is None or report.passed:
        return ""
    failed = next((gate for gate in report.gates if not gate.passed), None)
    return failed.reason if failed is not None else ""
