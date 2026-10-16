"""Promote a CELL-scope tool through the Queen when this Warden has no registry of its own.

On the Hive Stand the Warden holds the Comb Registry and promotes in process. A Cell's Warden does
not: the Hive's one registry is the Queen's (ADR-0046), so it announces its promotion to her and
she writes the row, building the principal from the link the message arrived on and never from
its text. `RemoteCombRegistry` is the `CombRegistry` the Warden's `PromotionFlow` is handed in that
case. `promote` runs the registry's own authority and evidence checks first (a Warden never sends
a promotion its own rules would refuse), then sends the Queen a `tool.promoted` naming this Warden
and its Cell, and waits two seconds for a `control.error` (`failed_kind` `tool.promoted`) -- the
announcement is an event, so silence means she accepted it. A refusal is raised as
`PromotionRefusedError`; otherwise the change is built from the row it just announced, and the
announcement is remembered on the Warden's tool relay, so a refusal that arrives after the wait
(a slow link, a busy Queen) still retracts the tool from the Warden's table and its bees' bundles
(`hivemind.wardens.ticks.tools.relay`) instead of leaving the two registries disagreeing. The reads
answer from the rows this adapter promoted; retiring and rolling back are operator commands that
run on the Hive Stand, where the registry is.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Built by `cell_scope` when `WardenDeps.royal_jelly` is None. Calls
    into `hivemind.royal_jelly.registry` (rows, rules) and `pending` for the refusal future.

Key invariants:
    - It writes nothing durable itself: the Queen's registry is the only store of a promotion.
    - A promotion refused by the registry's own rules is never sent.
    - It waits `REFUSAL_WAIT_S` for a refusal and no longer: the flow is never held up by silence.
    - An announcement taken as accepted stays known to the relay, so a later refusal is a
      retraction and never a stray.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for who may promote what.
    - hivemind.royal_jelly.registry.protocol for the CombRegistry this satisfies.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any, NoReturn

from hivemind.common.tasks import reap
from hivemind.royal_jelly import (
    PromotionRefusedError,
    QuarantineReport,
    ToolNotFoundError,
    ToolSpec,
    WrongCellError,
)
from hivemind.royal_jelly.registry import (
    ChangeKind,
    CombToolRow,
    Principal,
    PromotionOffer,
    RegistryChange,
    ToolKey,
)
from hivemind.royal_jelly.registry.rules import WriteContext, build_promoted_row, check_promotion
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.tools.messages import promoted_from_row
from waggle.envelope import wrap
from waggle.ids import CellId
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolScope

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

REFUSAL_WAIT_S = 2.0  # An event has no reply: how long a refusing control.error is waited for.

__all__ = ["REFUSAL_WAIT_S", "RemoteCombRegistry"]


class RemoteCombRegistry:
    """A `CombRegistry` whose one writer announces a CELL-scope promotion to the Queen."""

    def __init__(self, warden: Warden) -> None:
        """Bind the adapter to the Warden whose Queen link, relay and clock it uses."""
        self._warden = warden
        self._rows: dict[tuple[str, str], CombToolRow] = {}  # (name, version) -> row promoted here.

    async def list_tools(
        self,
        scope: ToolScope | None = None,
        cell_id: CellId | None = None,
        *,
        current_only: bool = False,
    ) -> tuple[CombToolRow, ...]:
        """List the rows this Warden promoted, narrowed like `CombRegistry.list_tools`."""
        rows = [
            row
            for row in self._rows.values()
            if (scope is None or row.scope is scope)
            and (cell_id is None or row.cell_id == cell_id)
            and (row.is_current or not current_only)
        ]
        return tuple(sorted(rows, key=lambda row: (row.name, row.version)))

    async def get(
        self, name: str, scope: ToolScope, cell_id: CellId | None, version: str | None = None
    ) -> CombToolRow:
        """Return a row this Warden promoted; the current version when `version` is None.

        Raises:
            ToolNotFoundError: This Warden promoted no such tool or version.
        """
        wanted = [
            row
            for row in self._rows.values()
            if row.name == name
            and row.scope is scope
            and row.cell_id == cell_id
            and (row.version == version if version is not None else row.is_current)
        ]
        if not wanted:
            raise ToolNotFoundError(f"This Warden promoted no tool {name!r} version {version}.")
        return wanted[0]

    async def visible_tools(self, cell_id: CellId) -> tuple[CombToolRow, ...]:
        """Return the current rows this Warden promoted for `cell_id`."""
        return tuple(r for r in self._rows.values() if r.is_current and r.cell_id == cell_id)

    async def promote(
        self,
        spec: ToolSpec,
        report: QuarantineReport,
        offer: PromotionOffer,
        principal: Principal,
        reason: str,
    ) -> RegistryChange:
        """Announce a promotion to the Queen and return the change built from its row.

        Args:
            spec: The spec offered.
            report: The passing Comb report.
            offer: The package facts.
            principal: This Warden's own principal.
            reason: Why it is promoted.

        Returns:
            The change, with the row this adapter built from the evidence.

        Raises:
            PromotionRefusedError: The registry's own rules refuse it, the Queen is unreachable,
                or she answered the announcement with a refusal.
        """
        # The same authority and evidence rules the registry applies, so nothing it would refuse
        # is ever sent; they raise their specific PromotionRefusedError subclass.
        check_promotion(spec, report, offer, principal)
        warden, cell_id = self._warden, spec.cell_id
        if cell_id is None:
            raise WrongCellError(
                f"Tool {spec.name} names no Cell; a Warden promotes at CELL scope."
            )
        displaced = self._current_version(spec)
        context = WriteContext(principal, reason, warden._deps.clock.now())
        row = build_promoted_row(spec, report, offer, context, displaced)
        error = await self._announce(promoted_from_row(row, cell_id))
        if error is not None:
            _refused(spec, error)
        self._store(row, displaced)
        return RegistryChange(
            ChangeKind.PROMOTED, row, displaced, principal.label, reason, context.at
        )

    async def retire(
        self, key: ToolKey, version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Refuse: retiring is an operator command, and the registry is on the Hive Stand."""
        raise NotImplementedError(
            "A Cell's Warden cannot retire a tool: the Comb Registry is the Queen's, and "
            "operator commands (`hive tools retire`) run on the Hive Stand."
        )

    async def rollback(
        self, key: ToolKey, to_version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Refuse: rolling back is an operator command, and the registry is on the Hive Stand."""
        raise NotImplementedError(
            "A Cell's Warden cannot roll a tool back: the Comb Registry is the Queen's, and "
            "operator commands (`hive tools rollback`) run on the Hive Stand."
        )

    async def _announce(self, announcement: ToolPromoted) -> ErrorMessage | None:
        """Send `announcement` and wait out the refusal window; None when the Queen stayed silent.

        Raises:
            PromotionRefusedError: The Queen is unreachable, so nothing was announced.
        """
        warden = self._warden
        envelope = wrap(announcement, warden._deps.hop, clock=warden._deps.clock)
        refusal = warden._tool_relay.expect(envelope.id)
        if not await send_guarded(warden._deps.queen_link, envelope):
            warden._tool_relay.discard(envelope.id)
            raise PromotionRefusedError("The Queen is unreachable; the promotion was not sent.")
        try:
            error = await self._refusal_within(refusal)
        finally:
            warden._tool_relay.discard(envelope.id)  # Answered, lapsed or cancelled: forget it.
        if error is None:
            # Silence is acceptance, but not proof of it: what the announcer is about to install
            # over (the entry of this name, if any) is kept so a late refusal can put it back.
            warden._tool_relay.announced(
                envelope.id, announcement, warden._comb_entries.get(announcement.name)
            )
        return error

    async def _refusal_within(self, future: asyncio.Future[Any]) -> ErrorMessage | None:
        """Wait up to `REFUSAL_WAIT_S` for the Queen's refusal; None when she stayed silent."""
        sleeper = asyncio.ensure_future(self._warden._deps.clock.sleep(REFUSAL_WAIT_S))
        try:
            # External await: a refusal arrives within one round trip if it arrives at all.
            await asyncio.wait({future, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap(sleeper)
        reply = future.result() if future.done() else None
        return reply if isinstance(reply, ErrorMessage) else None

    def _current_version(self, spec: ToolSpec) -> str | None:
        """Return the version this adapter last promoted under `spec`'s name, if any."""
        current = [r for r in self._rows.values() if r.name == spec.name and r.is_current]
        return current[0].version if current else None

    def _store(self, row: CombToolRow, displaced: str | None) -> None:
        """Remember `row` as current, and the version it displaced as no longer current."""
        if displaced is not None:
            old = self._rows[(row.name, displaced)]
            self._rows[(row.name, displaced)] = _not_current(old)
        self._rows[(row.name, row.version)] = row


def _not_current(row: CombToolRow) -> CombToolRow:
    """Return `row` with `is_current` cleared (rows are frozen dataclasses)."""
    return replace(row, is_current=False)


def _refused(spec: ToolSpec, error: ErrorMessage) -> NoReturn:
    """Raise the typed refusal for the Queen's `control.error` about a promotion."""
    raise PromotionRefusedError(
        f"The Queen refused the promotion of {spec.name} {spec.version} ({error.code}): "
        f"{error.message}"
    )
