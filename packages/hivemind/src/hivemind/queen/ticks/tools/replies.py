"""Answer a Warden's tool or Basket message: stable error codes, `control.error`, `tool.promoted`.

Every refusal the Queen's tool handlers make is one `control.error` correlated to the message it
refuses, carrying one of a few stable codes the Warden matches on (`hive.tool.request_refused`, a
failed validation; `hive.tool.rejected`, the Quarantine Comb or the registry said no;
`hive.tool.sandbox_unavailable`, no sandbox could be reached; `hive.tool.package_missing`, a
sandbox request for a package the Basket does not hold; `hive.tool.wrong_principal`, a Warden
claimed hive-scope authority). A success is a `tool.promoted` built from the registry row, so a
Warden reads the same fields wherever the announcement came from.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by every sibling handler; calls into `hivemind.royal_jelly.registry` (the row),
    `hivemind.queen.deps` (WardenLink) and waggle only.

Key invariants:
    - A reply is sent through `WardenLink.send`, so a link that closed since the message arrived
      is logged and dropped, never raised into the Queen's tick.
    - An error's text names ids and gate names, never a path, a traceback or a model's words.
    - `promoted_message` is a pure function of a registry row and the report that proved it.

See Also:
    - docs/waggle/spec.md section 8.8 for `tool.promoted` and the error codes.
    - hivemind.queen.ticks.honey for the same reply shape on the Honey path.
"""

from __future__ import annotations

from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.royal_jelly.registry import CombToolRow
from hivemind.royal_jelly.spec import QuarantineReport
from waggle.envelope import wrap
from waggle.ids import CellId, MessageId, WardenId
from waggle.messages.base import WaggleMessage
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS, ErrorMessage
from waggle.messages.tool import ToolPromoted, ToolScope

REQUEST_REFUSED = "hive.tool.request_refused"  # The request failed validation at the door.
REJECTED = "hive.tool.rejected"  # The Quarantine Comb or the registry refused the tool.
SANDBOX_UNAVAILABLE = "hive.tool.sandbox_unavailable"  # No sandbox could prove the tool.
PACKAGE_MISSING = "hive.tool.package_missing"  # A sandbox request for a package not in the Basket.
WRONG_PRINCIPAL = "hive.tool.wrong_principal"  # A Warden claimed authority over a hive-scope tool.

__all__ = [
    "PACKAGE_MISSING",
    "REJECTED",
    "REQUEST_REFUSED",
    "SANDBOX_UNAVAILABLE",
    "WRONG_PRINCIPAL",
    "promoted_message",
    "refuse",
    "reply",
]


async def reply(
    deps: QueenDeps, link: WardenLink, message: WaggleMessage, correlation_id: MessageId | None
) -> bool:
    """Send `message` to `link`'s Warden, correlated to the message it answers (if any).

    Args:
        deps: The Queen's collaborators; `clock` mints the envelope.
        link: The Warden's link.
        message: Any Waggle message: a reply, or an event such as `tool.promoted`.
        correlation_id: The envelope id it answers; None for an uncorrelated announcement.

    Returns:
        True once handed to the link; False, logged, when the link had already gone.
    """
    # Queued at once on an in-process pair or handed to the WebSocket's buffer; never awaited on
    # the far side, so replying is never the slow half of a round trip.
    return await link.send(wrap(message, link.hop, clock=deps.clock, correlation_id=correlation_id))


async def refuse(
    deps: QueenDeps,
    link: WardenLink,
    refused: tuple[str, str],
    code: str,
    text: str,
) -> bool:
    """Answer a refused message with `control.error`, correlated to it.

    Args:
        deps: The Queen's collaborators.
        link: The Warden to tell.
        refused: `(envelope id, kind)` of the message being refused.
        code: A stable `hive.*` code (this module's constants, or a registry refusal's own).
        text: One sentence naming the ids and the rule; cut to the wire's bound.

    Returns:
        True once handed to the link; False when it had gone.
    """
    envelope_id, kind = refused
    error = ErrorMessage(
        code=code,
        message=text[:MAX_ERROR_MESSAGE_CHARS],
        failed_kind=kind,
        # Only a changed request could succeed; resending the same one never does.
        is_retryable=False,
    )
    return await reply(deps, link, error, MessageId(envelope_id))


def promoted_message(row: CombToolRow, report: QuarantineReport, stand: CellId) -> ToolPromoted:
    """Build the `tool.promoted` a registry row and its proving report announce.

    Args:
        row: The PROMOTED row; its stored spec supplies the description, schema and platforms.
        report: The passing report that proved it.
        stand: The Hive Stand's Cell id, named as the Cell that proved it when the report ran in
            the development subprocess sandbox and so names none.

    Returns:
        The announcement; a CELL-scope row names its Cell and promoting Warden, a HIVE one neither.
    """
    spec = row.spec
    cell_scoped = row.scope is ToolScope.CELL
    return ToolPromoted(
        tool_id=row.tool_id,
        name=row.name,
        version=row.version,
        scope=row.scope,
        cell_id=row.cell_id,
        description=spec.description,
        input_schema_json=spec.input_schema_json,
        required_capabilities=spec.required_capabilities,
        supported_os=spec.supported_os,
        quarantine_path=row.quarantine_path,
        quarantined_on=report.sandbox_cell_id or stand,
        quarantine_passed_at=report.finished_at,
        package_sha256=row.package_sha256,
        package_size_bytes=row.package_size,
        # The registry stores a Warden's id as its label (QueenPrincipal's is the word "queen").
        promoted_by_warden=WardenId(row.promoted_by) if cell_scoped else None,
        supersedes_version=row.supersedes_version,
        reason=row.reason,
    )
