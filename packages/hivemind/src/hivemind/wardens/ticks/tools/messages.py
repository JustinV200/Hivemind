"""Build and send the messages the Warden's tool handlers exchange: errors, promotions, answers.

Three small things every tool handler needs. `tool_error` builds the `control.error` a Warden
answers an asker (or a Queen's order) with, carrying one of the stable `hive.tool.*` codes and the
`tool.*` kind it is about. `answer_asker` sends any payload down to a sub-bee as a fresh envelope
correlated to the request that sub-bee made (the Worker's tool channel matches a promotion by its
tool id and an error by that correlation). `promoted_from_row` turns a Comb Registry row into the
`tool.promoted` announcement the Warden sends the Queen or its own sub-bee, so the message is built
from the registry's stored evidence and never from anything a caller claims.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Called by `relay`, `cell_scope`, `ports`, `remote_registry` and
    `quarantine_run`. Calls into `hivemind.royal_jelly.registry` (the row), `hivemind.wardens.links`
    and waggle.

Key invariants:
    - Every error here has a stable code under `hive.tool.` and a full-sentence message with ids
      only: never a path, a traceback or tool source.
    - A reply to a sub-bee is a fresh envelope from this Warden to that sub-bee: the payload is
      unchanged, the id, addresses and correlation are this hop's own (spec section 3).

See Also:
    - docs/waggle/spec.md section 8.8 for ToolPromoted and 8.11 for control.error.
    - hivemind.workers.runtime.tools for how a Worker matches the answers built here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.royal_jelly.promotion import ToolIdentity
from hivemind.royal_jelly.registry import CombToolRow
from hivemind.wardens.links import send_guarded
from waggle.envelope import Hop, wrap
from waggle.ids import CellId, MessageId, WardenId, WorkerId
from waggle.messages.base import MAX_REASON_CHARS, WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolScope

if TYPE_CHECKING:
    from hivemind.wardens.deps import WardenDeps
    from hivemind.wardens.warden import Warden

REQUEST_KIND = "tool.request"  # The failed_kind of an error that refuses a tool request.
QUARANTINE_KIND = "tool.quarantine"  # ...of one about a sandbox request or a Queen's order.
PROMOTED_KIND = "tool.promoted"  # ...of one about a promotion this Warden announced.

NOT_HANDED_REASON = (
    "The tool was promoted but its package is not resident on this Cell, so it was not handed to "
    "you; ask again later."
)

REQUEST_REFUSED = "hive.tool.request_refused"  # The request may not be made (identity, caps).
REJECTED = "hive.tool.rejected"  # The tool was built or tried and did not pass.
SANDBOX_UNAVAILABLE = "hive.tool.sandbox_unavailable"  # No sandbox could be provided at all.
SANDBOX_REFUSED = "hive.tool.sandbox_refused"  # This Warden's Cell may not host the Comb.
NO_SCAFFOLDER = "hive.tool.no_scaffolder"  # No model slot to write the tool is allowed here.
LOAD_REFUSED = "hive.tool.load_refused"  # Promoted, but its package is not resident here.
BAD_ORDER = "hive.tool.bad_order"  # A quarantine order that contradicts itself.
PACKAGE_UNAVAILABLE = "hive.tool.package_unavailable"  # The ordered package is not resident.

__all__ = [
    "BAD_ORDER",
    "LOAD_REFUSED",
    "NOT_HANDED_REASON",
    "NO_SCAFFOLDER",
    "PACKAGE_UNAVAILABLE",
    "PROMOTED_KIND",
    "QUARANTINE_KIND",
    "REJECTED",
    "REQUEST_KIND",
    "REQUEST_REFUSED",
    "SANDBOX_REFUSED",
    "SANDBOX_UNAVAILABLE",
    "answer_asker",
    "promoted_from_row",
    "tool_error",
    "tool_identity",
]


def tool_identity(deps: WardenDeps) -> ToolIdentity:
    """Return the Hive, node and actor this Warden stamps on the `tool.*` events it records."""
    return ToolIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )


def tool_error(
    code: str, message: str, *, failed_kind: str = REQUEST_KIND, retryable: bool = False
) -> ErrorMessage:
    """Build a `control.error` about a tool message.

    Args:
        code: One of the `hive.tool.*` codes above.
        message: A full sentence with the ids needed to debug; never a path or a traceback.
        failed_kind: The `tool.*` kind of the message that failed.
        retryable: Whether asking again may succeed.

    Returns:
        The error, ready to be wrapped in a reply correlated to the message it answers.
    """
    return ErrorMessage(code=code, message=message, failed_kind=failed_kind, is_retryable=retryable)


async def answer_asker(
    warden: Warden, worker_id: WorkerId, payload: WaggleMessage, correlation_id: MessageId
) -> bool:
    """Send `payload` down to the sub-bee `worker_id` as the answer to its envelope.

    Args:
        warden: The answering Warden.
        worker_id: The sub-bee that asked.
        payload: A `ToolPromoted` or a `control.error` about its request.
        correlation_id: The id of the envelope the sub-bee sent, which an error must name.

    Returns:
        True when the frame reached the sub-bee's link; False when the sub-bee has ended or its
        link has closed (nobody is left to read it, which is not an error).
    """
    sub_bee = warden._sub_bees.get(worker_id)
    if sub_bee is None:
        return False
    hop = Hop(sender=warden._warden_id, recipient=worker_id, node_id=warden._deps.identity.node_id)
    envelope = wrap(payload, hop, clock=warden._deps.clock, correlation_id=correlation_id)
    return await send_guarded(sub_bee.link, envelope)


def promoted_from_row(row: CombToolRow, fallback_cell: CellId) -> ToolPromoted:
    """Build the `tool.promoted` announcement for a registry row, from its stored evidence.

    Args:
        row: A PROMOTED row, from the registry or built by this Warden's remote adapter.
        fallback_cell: The Cell named as `quarantined_on` when the report came from the
            development subprocess sandbox, which has no Cell.

    Returns:
        The announcement; a CELL-scope row names its Cell and the Warden that promoted it.
    """
    spec, report = row.spec, row.report
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
        quarantined_on=report.sandbox_cell_id or fallback_cell,
        quarantine_passed_at=report.finished_at,
        package_sha256=row.package_sha256,
        package_size_bytes=row.package_size,
        # The registry stores the promoting principal's label, which for a Warden is its id.
        promoted_by_warden=WardenId(row.promoted_by) if cell_scoped else None,
        supersedes_version=row.supersedes_version,
        reason=row.reason[:MAX_REASON_CHARS],
    )
