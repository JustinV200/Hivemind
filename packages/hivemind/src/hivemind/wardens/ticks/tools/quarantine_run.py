"""Run the Quarantine Comb on this Warden's own Cell when the Queen orders it (ADR-0047).

The Comb runs where its sandbox is, driven by that Cell's own Warden: no Warden answers `session.*`
messages before phase 11, so the Queen cannot drive a sandbox Cell's terminal herself. Instead she
ships the package here as a Basket blob and sends a `tool.quarantine` naming it by hash. This
module is the Warden's half. In the tick it checks the order is hers to give and well-formed (it
came from the Queen; its spec round-trips and agrees with the order; the package is resident and
hashes to the digest named), chooses a sandbox -- this Warden's own `CellSession` through
`SandboxCell` when its Cell is not borrowed, the development `SandboxSubprocess` only when the
manifest says `sandbox = "subprocess"`, otherwise it refuses with `hive.tool.sandbox_refused` --
and starts the run as a background job (six gates take far longer than a tick). The job replies
`tool.quarantine_report` correlated to the order's envelope and records `tool.quarantined`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Called by `hivemind.wardens.ticks.dispatch.act` for every
    `RUN_QUARANTINE`. Calls into `hivemind.royal_jelly` (the Comb, the sandboxes, the spec),
    `hivemind.wardens.ticks.basket` (the package read) and waggle.

Key invariants:
    - A Real (borrowed) Cell never hosts the Comb: `SandboxCell` refuses one and so does this
      choice; the subprocess sandbox runs only under an explicit manifest setting.
    - The package run is exactly the bytes the Warden holds, re-hashed against the order's digest.
    - Every order is answered: a report on a run, a `control.error` on any refusal.
    - The Comb records nothing itself; this module records `tool.quarantined` and relays.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for the decision and the gates.
    - hivemind.royal_jelly.quarantine_comb for the Comb and the sandboxes.
    - hivemind.queen.ticks.tools for the Queen's half.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.manifest.schema.royal_jelly import SandboxKind
from hivemind.royal_jelly import (
    QuarantineComb,
    QuarantineLimits,
    QuarantineReport,
    Sandbox,
    SandboxCell,
    SandboxSubprocess,
    ToolSpec,
    sha256_of,
)
from hivemind.royal_jelly.promotion import quarantined_event
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.links import QUEEN_LINK, send_guarded
from hivemind.wardens.ticks.basket.events import own_cell_id
from hivemind.wardens.ticks.basket.jobs import start_job
from hivemind.wardens.ticks.basket.source import package_source
from hivemind.wardens.ticks.tools.messages import (
    BAD_ORDER,
    PACKAGE_UNAVAILABLE,
    QUARANTINE_KIND,
    SANDBOX_REFUSED,
    tool_error,
    tool_identity,
)
from hivemind.workers.tools.errors import PackageUnavailableError
from waggle.envelope import wrap
from waggle.errors import WaggleError
from waggle.ids import CellId, MessageId
from waggle.messages.control import ErrorMessage
from waggle.messages.tool import ToolQuarantine, ToolQuarantineReport

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["handle_quarantine_order"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Ordered:
    """What a run is for: the order, the sandbox chosen for it and the Cell the report names."""

    order: ToolQuarantine
    sandbox: Sandbox
    cell_id: CellId  # The Cell attributed in the report when the sandbox itself has none.


async def handle_quarantine_order(warden: Warden, item: InboxItem) -> None:
    """Check a Queen-sent `tool.quarantine`, pick a sandbox and start the Comb run.

    Args:
        warden: The Warden whose Cell hosts the sandbox.
        item: The ordered inbox item; its id is what the reply correlates to.
    """
    order = item.payload
    if item.principal != QUEEN_LINK or not isinstance(order, ToolQuarantine):
        # Only the Queen orders a sandbox run; a sub-bee's order is not the Warden's to obey.
        log.warning("wardens.quarantine.misdirected", principal=item.principal)
        return
    request_id = MessageId(item.id)
    checked = await _check(warden, order)
    if isinstance(checked, ErrorMessage):
        await _reply(warden, request_id, checked)
        return
    spec, package, limits = checked
    sandbox, cell_id = _sandbox(warden, limits), own_cell_id(warden)
    if sandbox is None or cell_id is None:
        reason = (
            "This Warden's Cell cannot host the Quarantine Comb and no development sandbox is set."
        )
        refusal = tool_error(SANDBOX_REFUSED, reason, failed_kind=QUARANTINE_KIND)
        await _reply(warden, request_id, refusal)
        return
    ordered = _Ordered(order, sandbox, cell_id)
    start_job(warden, _run(warden, (spec, package, limits), ordered, request_id))


async def _check(
    warden: Warden, order: ToolQuarantine
) -> tuple[ToolSpec, bytes, QuarantineLimits] | ErrorMessage:
    """Parse the order and read its package, or return the error that answers it."""
    try:
        spec = ToolSpec.from_json(order.spec_json)
        limits = QuarantineLimits.from_json(order.limits_json)
    except ValidationError:
        return tool_error(
            BAD_ORDER,
            f"The spec or limits of the order for {order.name} do not parse.",
            failed_kind=QUARANTINE_KIND,
        )
    if (spec.tool_id, spec.name, spec.version) != (order.tool_id, order.name, order.version):
        return tool_error(
            BAD_ORDER,
            f"The order for {order.name} contradicts its own spec.",
            failed_kind=QUARANTINE_KIND,
        )
    source = package_source(warden._deps)
    try:
        # Local disk read of the resident blob, re-hashed; a missing blob is refused, not awaited.
        package = await source.bytes_for(order.package_sha256) if source is not None else None
    except PackageUnavailableError:
        package = None
    if package is None or sha256_of(package) != order.package_sha256:
        return tool_error(
            PACKAGE_UNAVAILABLE,
            f"Package {order.package_sha256} is not resident on this Cell.",
            failed_kind=QUARANTINE_KIND,
            retryable=True,
        )
    return spec, package, limits


def _sandbox(warden: Warden, limits: QuarantineLimits) -> Sandbox | None:
    """Choose where the Comb runs: this Cell's session, else the dev subprocess, else nowhere."""
    deps, cell, session = warden._deps, warden._cell, warden._session
    # A Cell that is not borrowed hosts the Comb through its own session (SandboxCell would refuse
    # a Real Cell; the question is asked of the Cell's capability, never of its kind).
    if cell is not None and session is not None and not cell.is_borrowed:
        return SandboxCell(cell, session, deps.clock, output_bytes=limits.output_bytes)
    access = deps.royal_jelly
    if access is not None and access.sandbox_kind is SandboxKind.SUBPROCESS:
        return SandboxSubprocess(deps.clock, output_bytes=limits.output_bytes)
    return None


async def _run(
    warden: Warden,
    work: tuple[ToolSpec, bytes, QuarantineLimits],
    ordered: _Ordered,
    request_id: MessageId,
) -> None:
    """Background job: run the Comb, record `tool.quarantined` and reply with the report."""
    spec, package, limits = work
    deps = warden._deps
    try:
        # External await: up to the wall limit plus a grace (minutes); the Comb bounds itself and
        # always cleans the sandbox up.
        report = await QuarantineComb(deps.clock).run(
            package, spec, ordered.sandbox, limits, ordered.order.path
        )
    except (HiveMindError, WaggleError, OSError) as exc:
        log.warning("wardens.quarantine.run_failed", tool_id=spec.tool_id, error=repr(exc))
        message = f"The Comb could not run for {spec.name}."
        failure = tool_error(SANDBOX_REFUSED, message, failed_kind=QUARANTINE_KIND, retryable=True)
        await _reply(warden, request_id, failure)
        return
    await deps.trail.record(quarantined_event(tool_identity(deps), deps.clock, report))
    await _reply(warden, request_id, _report_message(report, ordered.cell_id))


def _report_message(report: QuarantineReport, fallback_cell: CellId) -> ToolQuarantineReport:
    """Build the `tool.quarantine_report` for a finished run."""
    failed = next((gate for gate in report.gates if not gate.passed), None)
    return ToolQuarantineReport(
        tool_id=report.tool_id,
        version=report.version,
        passed=report.passed,
        path=report.path,
        sandbox_cell_id=report.sandbox_cell_id or fallback_cell,
        failing_gate=report.failing_gate,
        report_json=report.to_json(),
        reason=(failed.reason if failed is not None and failed.reason else "every gate passed")[
            :500
        ],
    )


async def _reply(
    warden: Warden, request_id: MessageId, payload: ToolQuarantineReport | ErrorMessage
) -> None:
    """Send the Queen the report or error that answers her order `request_id`."""
    envelope = wrap(payload, warden._deps.hop, clock=warden._deps.clock, correlation_id=request_id)
    await send_guarded(warden._deps.queen_link, envelope)
