"""Define Offering: put a hosted model on the Forage map and the Queen, take it off, load one more.

A Warden (a Cell's always-on supervisor) that hosts a model server offers each model it serves in
two places at once: the live Forage map (the list of every source that can serve a model) its own
sub-bees are bound from, and the Queen (the Hive's central orchestrator), who is told with
``forage.source_added`` so she can count its seats. ``Offering`` is that pair of moves and its
reverse, plus the one other thing a host does with a source: ``load`` offers one more model of a
server that is already running. That is the Queen's rule of ceilings (codingrules 8.10) applied to
a single model: an allowlisted model within the VRAM ceiling loads without asking and is reported
as a new source and nothing else; a model outside the allowlist, or past the VRAM left, is
refused, and a refusal the Queen asked for (a promotion's first models, a later load) is recorded
as a ``guard.denied`` at the slot-binding point so the audit log shows a Warden saying no. The
class holds no state of its own: the host keeps the table of running servers and stores what
``load`` returns.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Built by ``host.ModelServerHost`` and called by it. Calls into
    ``config``, ``report``, ``sources``, ``hivemind.forage``, ``hivemind.guard`` and
    ``hivemind.common.logging`` only.

Key invariants:
    - A source is on the map exactly when the Queen was told of it: ``offer`` and ``withdraw`` do
      both, in that order for an offer and the reverse for a withdrawal.
    - Loading a source already on offer changes nothing and records nothing.
    - A refusal that is recorded is a Guard denial with a scope word, ids and counts; it carries
      no model name, path or address.
    - ``load`` never starts or stops a process: it only offers what a running server can serve.

See Also:
    - .claude/codingrules.md section 8.10 for "loading within the ceilings is autonomous".
    - hivemind.guard.enforcer for ``Enforcer.refuse``, the one way a point records a refusal.
    - hivemind.wardens.local_pool.hosting.sources for the ceilings rule and the ``Refusal``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.forage import ModelSlot, ModelSource
from hivemind.guard import (
    WARDEN_ROLE,
    Capability,
    CapabilityFamily,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    warden_principal,
)
from hivemind.wardens.local_pool.hosting.config import HostedCell, RunningServer
from hivemind.wardens.local_pool.hosting.report import ReportPort, announce_added, announce_removed
from hivemind.wardens.local_pool.hosting.sources import Refusal, hosted, narrow, plan_loads
from waggle.messages.forage import SourceRemovalCause

if TYPE_CHECKING:
    # Type-only: wardens.deps imports hosting.config, so a runtime import here would cycle.
    from hivemind.wardens.deps import WardenDeps

log = get_logger(__name__)

__all__ = ["Offering"]


@dataclass(frozen=True, slots=True)
class Offering:
    """What a host offers, withdraws and loads sources with: one Warden's collaborators.

    Attributes:
        deps: The Warden's collaborators: its Forage map, its enforcer and its Guard roles.
        place: Its Cell and a reader of the Queen's current ceilings.
        port: Where it tells the Queen and records on the trail.
    """

    deps: WardenDeps
    place: HostedCell
    port: ReportPort

    async def offer(self, server_id: str, source: ModelSource) -> None:
        """Put ``source`` on the live Forage map and tell the Queen it is servable.

        Args:
            server_id: The ``[llm.servers]`` key of the server that serves it.
            source: The source, its ``host_cell_id`` already this Cell.
        """
        if self.deps.map is not None:
            await self.deps.map.add_source(source)
        reason = f"Loaded on {server_id} within this Warden's ceilings."
        await announce_added(self.port, server_id, source, reason)

    async def withdraw(
        self, server_id: str, source: ModelSource, cause: SourceRemovalCause
    ) -> None:
        """Take ``source`` off the live map and tell the Queen it is gone.

        Args:
            server_id: The server it was offered from.
            source: The source that leaves.
            cause: Why it left, as the Queen is told.
        """
        if self.deps.map is not None:
            await self.deps.map.remove_source(source.source_id)
        reason = f"Source left: {cause.value}."
        await announce_removed(self.port, server_id, source, cause, reason)

    async def load(
        self, server: RunningServer, source_id: str, in_use: int
    ) -> RunningServer | None:
        """Offer one more of a running server's sources, when the ceilings let this Warden.

        Args:
            server: The running server, as the host holds it.
            source_id: The Forage map source to offer.
            in_use: The VRAM the sources already offered, on every server, occupy.

        Returns:
            ``server`` grown by the source (or unchanged, when it was already on offer); None when
            the source may not be loaded, after the refusal was recorded.
        """
        if any(offered.source_id == source_id for offered in server.sources):
            return server  # Already on offer: asking twice changes nothing and says nothing.
        server_id = server.config.server_id
        wanted, unknown = narrow(server.config.sources, (source_id,))
        plan = plan_loads(wanted, self.place.ceilings_of(), in_use)
        # A load someone asked for is a refusal worth an audit row, not only a log line.
        await self.refuse(server_id, (*unknown, *plan.refused), record=True)
        if not plan.load:
            return None
        loaded = hosted(plan.load[0], self.place.cell.id)
        await self.offer(server_id, loaded)
        return replace(server, sources=(*server.sources, loaded))

    async def refuse(self, server_id: str, refusals: Iterable[Refusal], *, record: bool) -> None:
        """Log each refusal and, when ``record``, write it to the trail as a Guard denial.

        Args:
            server_id: The server the sources belong to.
            refusals: What the ceilings (or the server's own list) kept out.
            record: Whether the refusal answers a request, and so belongs on the trail; a server
                that merely lists more models than the ceilings allow is only logged.
        """
        for refusal in refusals:
            log.info(
                "wardens.hosting.refused",
                server_id=server_id,
                source_id=refusal.source_id,
                scope=refusal.scope,
                why=refusal.why,
            )
            if record:
                # Local trail write: the same awaited, durable record every Guard denial makes.
                await self.deps.enforcer.refuse(self._request(), refusal.scope, refusal.why)

    def _request(self) -> PolicyRequest:
        """Build the request a refused load is recorded under: this Warden binding a model."""
        cell = self.place.cell
        return PolicyRequest(
            principal=warden_principal(self.port.warden_id),
            point=EnforcementPoint.SLOT_BINDING,
            # A model is loaded to be bound for a sub-bee's work; the slot is the one a Drone uses.
            needed=Capability(family=CapabilityFamily.LLM, scope=ModelSlot.WORKER.manifest_key),
            held=self.deps.guard.roles[WARDEN_ROLE].allow,
            context=PolicyContext(comb_shield=cell.comb_shield, access_level=cell.access_level),
        )
