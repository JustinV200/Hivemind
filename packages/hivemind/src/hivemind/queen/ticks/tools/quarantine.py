"""Broker a Warden's sandbox request, and route a sandbox Warden's report to the job awaiting it.

A Warden that scaffolded a cell-scope tool cannot provision a sandbox itself (Wardens never
provision Cells, ADR-0047), so it ships the package up to the Basket and sends the Queen a
`tool.quarantine`: that message is its sandbox request. This module is the door: the sender must
be the Warden the request names, for a cell-scope tool on its own Cell, with a spec that parses,
matches the request and stays inside the Warden's own capabilities, and a package the Basket
already holds. A passing request starts a job that runs `QueenSandboxRunner` (provision, ship,
order, await, release) and relays the Comb's report back as a `tool.quarantine_report` correlated
to the request; an unreachable sandbox is `control.error hive.tool.sandbox_unavailable`. The other
half is the inbound `tool.quarantine_report` from a sandbox Cell's Warden: it resolves the future
the runner's job awaits, in a later tick than the order was sent.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by the tools dispatch; calls into `.runner`, `.jobs`, `.replies` and `hivemind.royal_jelly`
    (the spec checks), `hivemind.queen.authority` (the Warden's held set).

Key invariants:
    - The tick starts a job or resolves a future; it never waits for a sandbox.
    - A report resolves only the order sent to the Warden that answers it; any other sender, or
      a correlation the Queen never sent, is logged and dropped.
    - The Queen keeps the spec and the report of every run, because she promotes (or lets a
      Warden promote) only on what she holds.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for the brokered sandbox request.
    - hivemind.queen.ticks.tools.runner for the run the job performs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from hivemind.basket import BasketError, BlobNotFoundError
from hivemind.cell import hive_stand_cell_id
from hivemind.common.logging import get_logger
from hivemind.queen.authority import warden_held
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.errors import SandboxUnavailableError
from hivemind.queen.ticks.tools import replies
from hivemind.queen.ticks.tools.jobs import start_job
from hivemind.queen.ticks.tools.runner import QueenSandboxRunner
from hivemind.royal_jelly import RoyalJellyAccess
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec, validate_spec
from hivemind.supervision.attendant import InboxItem
from waggle.ids import CellId, MessageId, WardenId
from waggle.messages.tool import ToolQuarantine, ToolQuarantineReport, ToolScope

log = get_logger(__name__)

__all__ = ["handle_quarantine_report", "handle_tool_quarantine"]


@dataclass(frozen=True, slots=True)
class _Refusal:
    """Why a sandbox request was refused at the door: a stable code and one sentence."""

    code: str
    text: str


async def handle_tool_quarantine(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Validate a Warden's sandbox request and start the job that brokers it.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered item; `item.principal` is the requesting Warden, `item.id` the envelope
            id the report or the error is correlated to.

    Returns:
        True when `item.payload` was a `ToolQuarantine` (answered, or its job started); False
        otherwise.
    """
    order = item.payload
    if not isinstance(order, ToolQuarantine):
        return False
    link = wardens.get(WardenId(item.principal))
    if link is None:
        return True  # The Warden detached since: nobody to answer.
    refused = (item.id, item.payload_kind)
    access = deps.royal_jelly
    if access is None:
        text = "No Royal Jelly Lab is wired to this Hive."
        await replies.refuse(deps, link, refused, replies.SANDBOX_UNAVAILABLE, text)
        return True
    checked = await _check(deps, access, link, order)
    if isinstance(checked, _Refusal):
        await replies.refuse(deps, link, refused, checked.code, checked.text)
    elif not start_job(deps, order.tool_id, _broker(deps, link, item, checked)):
        text = f"Tool {order.tool_id} is already in flight, or the Queen's tool lane is full."
        await replies.refuse(deps, link, refused, replies.REQUEST_REFUSED, text)
    return True


async def _check(
    deps: QueenDeps, access: RoyalJellyAccess, link: WardenLink, order: ToolQuarantine
) -> tuple[ToolSpec, ToolQuarantine] | _Refusal:
    """Check the request: who sent it, what it names, and that the Basket holds the package."""
    if order.requested_by_warden != link.warden_id:
        return _Refusal(
            replies.REQUEST_REFUSED, "A sandbox request must name the Warden that sent it."
        )
    if order.scope is not ToolScope.CELL or order.cell_id != link.cell.id:
        return _Refusal(
            replies.REQUEST_REFUSED,
            "A Warden may ask for a sandbox only for a cell-scope tool on its own Cell.",
        )
    try:
        spec = ToolSpec.from_json(order.spec_json)
    except (ValidationError, ValueError):
        return _Refusal(replies.REQUEST_REFUSED, "The request's spec is not a tool spec.")
    if not _agrees(spec, order, link):
        return _Refusal(
            replies.REQUEST_REFUSED, "The request's spec does not match its own fields."
        )
    verdict = validate_spec(spec, warden_held(deps, link.cell))
    if not verdict.ok:
        return _Refusal(replies.REQUEST_REFUSED, "; ".join(verdict.reasons))
    try:
        # Local SQLite: is the package in the Basket? Its bytes are read in the job.
        blob = await access.basket.get(order.package_sha256)
    except BlobNotFoundError:
        return _Refusal(
            replies.PACKAGE_MISSING, f"Package {order.package_sha256} is not in the Basket."
        )
    if blob.size_bytes != order.package_size_bytes:
        return _Refusal(replies.REQUEST_REFUSED, "The package is not the size the request names.")
    return spec, order


def _agrees(spec: ToolSpec, order: ToolQuarantine, link: WardenLink) -> bool:
    """Return whether the spec is the one the request's own fields describe, for this Warden."""
    return (
        spec.tool_id == order.tool_id
        and spec.name == order.name
        and spec.version == order.version
        and spec.scope is order.scope
        and spec.cell_id == order.cell_id
        and spec.requested_by == link.warden_id
    )


async def _broker(
    deps: QueenDeps, link: WardenLink, item: InboxItem, request: tuple[ToolSpec, ToolQuarantine]
) -> None:
    """The job: run the Comb in a sandbox for the Warden and relay the report, or say why not."""
    spec, order = request
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - the door refused a Hive with no Lab.
    refused = (item.id, item.payload_kind)
    try:
        # Disk latency class, bounded by the package size (the Basket enforced its cap at deposit).
        package = await access.basket.open_bytes(order.package_sha256)
    except BasketError as exc:
        text = f"Package {order.package_sha256} could not be read from the Basket ({exc.code})."
        await replies.refuse(deps, link, refused, replies.PACKAGE_MISSING, text)
        return
    try:
        # External await: provision, ship, order and the sandbox Warden's report; bounded by
        # `[royal_jelly] request_timeout_s` inside the runner.
        report = await QueenSandboxRunner(deps).quarantine(spec, package, order.path)
    except SandboxUnavailableError as exc:
        await replies.refuse(deps, link, refused, replies.SANDBOX_UNAVAILABLE, exc.reason)
        return
    stand = hive_stand_cell_id(deps.identity.node_id)
    await replies.reply(deps, link, _report_message(report, stand), MessageId(item.id))


def _report_message(report: QuarantineReport, stand: CellId) -> ToolQuarantineReport:
    """Build the `tool.quarantine_report` that relays `report` to the requesting Warden."""
    failed = next((gate for gate in report.gates if not gate.passed), None)
    reason = failed.reason if failed is not None else "Every gate passed."
    return ToolQuarantineReport(
        tool_id=report.tool_id,
        version=report.version,
        passed=report.passed,
        path=report.path,
        sandbox_cell_id=report.sandbox_cell_id or stand,
        failing_gate=report.failing_gate,
        report_json=report.to_json(),
        reason=reason[:500],
    )


async def handle_quarantine_report(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Hand a sandbox Warden's report to the job that ordered it.

    Args:
        deps: The Queen's collaborators; `tool_lane.pending_reports` holds the waiting futures.
        wardens: Every Warden currently attached (unused: the sender is checked by id).
        item: The ordered item; `item.correlation_id` names the order's envelope.

    Returns:
        True when `item.payload` was a `ToolQuarantineReport` (resolved or dropped); False
        otherwise.
    """
    del wardens  # The pending order names its Warden; the link table is not needed.
    report = item.payload
    if not isinstance(report, ToolQuarantineReport):
        return False
    pending = (
        deps.tool_lane.pending_reports.get(MessageId(item.correlation_id))
        if item.correlation_id is not None
        else None
    )
    if pending is None or pending.warden_id != WardenId(item.principal):
        # A report for an order the Queen never sent, a stale one, or one from the wrong Warden.
        log.warning(
            "queen.tools.report_unmatched", principal=item.principal, tool_id=report.tool_id
        )
        return True
    if not pending.future.done():
        pending.future.set_result(report)
    return True
