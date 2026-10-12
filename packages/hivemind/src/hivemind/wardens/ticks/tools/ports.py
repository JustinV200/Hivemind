"""Give a Warden's own promotion flow its three effectful ports: sandbox, Honey and announcement.

A CELL-scope tool is promoted by the Warden of the Cell it is for (ADR-0046), through the same
`PromotionFlow` the Queen runs at HIVE scope; the flow decides what happens next and these ports
do the work. `WardenSandboxRunner` proves a package: it deposits the package in the Basket
(the Hive's store on the Hive Stand, the Cell's own in a Cell, where it is also shipped up), sends
the Queen a `tool.quarantine` -- that message is this Warden's sandbox request (ADR-0047) -- and
awaits the correlated `tool.quarantine_report` on a future a later tick resolves, so no tick ever
waits on it. `WardenHoney` describes the promotion to the Honey Store as Nectar sent up the Queen
link, and `WardenAnnouncer` installs the new tool on this Warden's own sub-bees and tells the one
that asked. Each failure is raised as the typed error the flow already turns into a rejection or
logs, and none of them ever carries a tool's source or a package's bytes.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Built by `cell_scope` for each flow it starts and awaited only by
    that flow's background job. Calls into `hivemind.royal_jelly` (the ports' Protocols, the spec
    and the Honey link), `hivemind.basket`, `hivemind.wardens.ticks.basket` and waggle.

Key invariants:
    - The runner never returns a report it did not receive: a timeout, a refusal or an unreachable
      Queen raises, and the flow records a rejection.
    - A package is deposited (and, in a Cell, shipped up) before the Queen is asked to prove it,
      so the order names a blob she already holds.
    - The announcer tells the asker of a success only after the tool is installed.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for why the Queen provides the sandbox.
    - hivemind.royal_jelly.promotion.ports for the Protocols these satisfy.
    - hivemind.wardens.ticks.tools.cell_scope for where they are assembled.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ValidationError

from hivemind.basket import BasketError, DepositRequest, MediaKind
from hivemind.common.tasks import reap
from hivemind.royal_jelly import (
    PromotionSite,
    QuarantineLimits,
    QuarantineReport,
    RoyalJellyError,
    ToolSpec,
    promotion_submission,
    sha256_of,
)
from hivemind.royal_jelly.registry import RegistryChange
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.basket.door import deposit_door, send_nectar
from hivemind.wardens.ticks.basket.send import request_timeout_s, ship_up
from hivemind.wardens.ticks.tools.install import install_tool
from hivemind.wardens.ticks.tools.messages import (
    LOAD_REFUSED,
    NOT_HANDED_REASON,
    answer_asker,
    promoted_from_row,
    tool_error,
)
from hivemind.wardens.ticks.tools.pending import Asker, Reply
from hivemind.workers.nectar import DepositMeta
from waggle.envelope import wrap
from waggle.errors import ConnectionLostError
from waggle.ids import MessageId, TaskId
from waggle.messages import CombShieldLevel, HoneyClearance
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolQuarantine
from waggle.messages.tool.authoring import QuarantinePath

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

PACKAGE_MEDIA_TYPE = "application/zip"  # A tool package is a deterministic zip archive.

__all__ = [
    "PACKAGE_MEDIA_TYPE",
    "SandboxRequestError",
    "WardenAnnouncer",
    "WardenHoney",
    "WardenSandboxRunner",
]


class SandboxRequestError(RoyalJellyError):
    """Raise when this Warden could not get a verdict from a sandbox: the flow records a rejection.

    Its stable code is the one the Queen uses for the same failure, so the rejection an asker reads
    names it (`quarantine: hive.tool.sandbox_unavailable`).
    """

    code: ClassVar[str] = "hive.tool.sandbox_unavailable"


class WardenSandboxRunner:
    """A `SandboxRunner`: deposit the package, ask the Queen for a sandbox, await the report."""

    def __init__(self, warden: Warden, task_id: TaskId | None, reason: str) -> None:
        """Bind the runner to the Warden asking and the request it serves.

        Args:
            warden: The asking Warden.
            task_id: The task that needs the tool, for the package's provenance.
            reason: Why the tool is wanted (the request's reason), for the order.
        """
        self._warden = warden
        self._task_id = task_id
        self._reason = reason

    async def quarantine(
        self, spec: ToolSpec, package: bytes, path: QuarantinePath
    ) -> QuarantineReport:
        """Prove `package` against `spec` in the sandbox the Queen provides and return the report.

        Args:
            spec: The spec the package claims to satisfy.
            package: The package bytes.
            path: Which route through the Comb the request asks for.

        Returns:
            The Comb's report, passed or failed.

        Raises:
            RoyalJellyError: The package could not be deposited or shipped, the Queen is out of
                reach, refused or did not answer in time, or her reply was not a valid report.
        """
        await self._deposit(spec, package)
        order = self._order(spec, package, path)
        warden, deps = self._warden, self._warden._deps
        envelope = wrap(order, deps.hop, clock=deps.clock)
        # Registered before the envelope leaves, so the report can never outrun its waiter.
        future = warden._tool_relay.expect(envelope.id)
        if not await send_guarded(deps.queen_link, envelope):
            warden._tool_relay.discard(envelope.id)
            raise SandboxRequestError("The Queen is unreachable, so no sandbox could be requested.")
        try:
            reply = await self._await_reply(future, envelope.id)
        finally:
            # Whether it answered, timed out or this job was cancelled by `Warden.stop`.
            warden._tool_relay.discard(envelope.id)
        return _report_of(reply)

    async def _deposit(self, spec: ToolSpec, package: bytes) -> None:
        """Deposit the package in this Warden's Basket and, in a Cell, ship it up to the Queen."""
        warden = self._warden
        door, cell = deposit_door(warden), warden._cell
        if door is None or cell is None:
            raise SandboxRequestError("This Warden has no Basket to deposit the tool package in.")
        request = DepositRequest(
            content=package,
            title=f"Tool package {spec.name} {spec.version}",
            summary=f"Candidate package of the {spec.name} tool, {spec.scope.value} scope.",
            media_kind=MediaKind.TOOL_PACKAGE,
            media_type=PACKAGE_MEDIA_TYPE,
            origin_cell_id=cell.id,
            origin_task_id=self._task_id,
            origin_bee=warden._warden_id,
            from_borrowed_cell=cell.is_borrowed,
            tier=cell.comb_shield,
        )
        try:
            outcome = await door.deposit(request)
            for event in outcome.events:
                await warden._deps.trail.record(event)
            if warden._deps.resident_basket is not None and warden._deps.royal_jelly is None:
                await warden._deps.resident_basket.admit(outcome.blob)
        except BasketError as exc:
            raise SandboxRequestError(f"The tool package could not be deposited: {exc}") from exc
        if not await ship_up(warden, sha256_of(package), f"quarantine of tool {spec.name}"):
            raise SandboxRequestError("The tool package could not be shipped up to the Queen.")

    def _order(self, spec: ToolSpec, package: bytes, path: QuarantinePath) -> ToolQuarantine:
        """Build this Warden's sandbox request: the order the Queen runs on its behalf."""
        access = self._warden._deps.royal_jelly
        limits = access.limits if access is not None else QuarantineLimits()
        reason = self._reason[:MAX_REASON_CHARS] or f"Cell-scope tool {spec.name} requested."
        return ToolQuarantine(
            tool_id=spec.tool_id,
            name=spec.name,
            version=spec.version,
            scope=spec.scope,
            cell_id=spec.cell_id,
            spec_json=spec.to_json(),
            package_sha256=sha256_of(package),
            package_size_bytes=len(package),
            path=path,
            limits_json=limits.to_json(),
            requested_by_warden=self._warden._warden_id,
            reason=reason,
        )

    async def _await_reply(self, future: asyncio.Future[Reply], envelope_id: MessageId) -> Reply:
        """Wait, bounded, for the report or error correlated to the order; raise on a timeout."""
        warden = self._warden
        timeout = request_timeout_s(warden)
        sleeper = asyncio.ensure_future(warden._deps.clock.sleep(timeout))
        waitables: set[asyncio.Future[Any]] = {future, sleeper}
        try:
            # External await: the Queen provisions a sandbox and a Warden runs six gates; minutes
            # at worst. Past the limit the request is abandoned and the flow records a rejection.
            await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap(sleeper)
        if not future.done():
            raise SandboxRequestError(
                f"The Queen did not answer the sandbox request in {timeout:g}s."
            )
        return future.result()


class WardenHoney:
    """A `HoneyDeposit`: describe a promoted tool to the Honey Store as Nectar (Hive Stand only)."""

    def __init__(self, warden: Warden) -> None:
        """Send this Warden's promotion Nectar up its own Queen link."""
        self._warden = warden

    async def deposit_promotion(
        self, spec: ToolSpec, report: QuarantineReport, package_sha256: str
    ) -> None:
        """Send the Nectar describing a promotion, as `_forward_deposit` sends a Worker's.

        Args:
            spec: The promoted spec.
            report: The passing report that proved it.
            package_sha256: The promoted package's hash.

        Raises:
            waggle.errors.ConnectionLostError: The Queen link is gone; the promotion stands.
        """
        warden = self._warden
        cell = warden._cell
        # A Cell's Warden announces its promotion to the Queen, who describes the tool herself
        # when she accepts it; only the Hive Stand's, which promotes in process, sends Nectar.
        if cell is None or warden._deps.royal_jelly is None:
            return
        site = PromotionSite(cell.id, cell.comb_shield, cell.is_borrowed)
        sub = promotion_submission(spec, report, package_sha256, site, warden._deps.clock)
        declared = sub.declared or HoneyClearance.C0
        meta = DepositMeta(
            kind=sub.kind,
            media_type=sub.media_type,
            title=sub.title,
            task_id=sub.task_id,
            cell_id=sub.cell_id,
            worker_id=None,  # A Warden deposits this one.
            observed_at=sub.observed_at,
            clearance=HoneyClearance(declared.value),
            origin_tier=CombShieldLevel(sub.tier.value),
        )
        if not await send_nectar(warden, sub.content, meta):
            raise ConnectionLostError("The tool's Honey record could not be sent to the Queen.")


class WardenAnnouncer:
    """An `Announcer`: install the new tool on this Warden's own bees, then tell the asker."""

    def __init__(self, warden: Warden, asker: Asker) -> None:
        """Bind the announcer to the Warden and the sub-bee that asked for the tool."""
        self._warden = warden
        self._asker = asker

    async def announce(self, change: RegistryChange, spec: ToolSpec) -> None:
        """Install the promoted tool and forward the promotion to the sub-bee that asked.

        Args:
            change: What the registry did; `change.row` is the new current row.
            spec: The promoted spec (unused: the row carries the stored copy).
        """
        del spec  # The row's stored spec is the registry's evidence; the message is built from it.
        warden, asker = self._warden, self._asker
        cell = warden._cell
        if cell is None:
            return
        message = promoted_from_row(change.row, cell.id)
        installed = await install_tool(warden, message)
        answer = (
            message if installed else tool_error(LOAD_REFUSED, NOT_HANDED_REASON, retryable=True)
        )
        await answer_asker(warden, asker.worker_id, answer, asker.original_id)


def _report_of(reply: Reply) -> QuarantineReport:
    """Return the report a reply carries, or raise the typed error the flow records."""
    if isinstance(reply, ErrorMessage):
        raise SandboxRequestError(
            f"The Queen could not provide a sandbox ({reply.code}): {reply.message}"
        )
    try:
        return QuarantineReport.from_json(reply.report_json)
    except ValidationError as exc:
        raise SandboxRequestError("The Queen's quarantine report is not a valid report.") from exc
