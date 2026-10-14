"""Define ModelServerHost: start and stop the model servers a Warden hosts on its own Cell.

A Warden (a Cell's always-on supervisor) that holds its Cell's lease may start the OpenAI-compatible
**model servers** the Hive Manifest declares for it (``[llm.servers]``, ADR-0044, roadmap step 8.3)
and offer their models to the Hive. ``ModelServerHost`` is that mechanism, one per Warden. For a
server it: refuses at once when the Cell does not report ``can_host_model`` (branching on the
capability, never on the Cell's kind); plans which of the server's Forage map sources fit the
Queen's ceilings (``sources.plan_loads``); registers the provider the server answers as and starts
its process through the Warden's ``CellSession`` (``launch``); waits for the provider's own health
probe; and only then adds each source to the live Forage map, with this Cell as host, and tells the
Queen (``report``). Stopping reverses it in the opposite order, so nothing is routed to a server
that is going away: sources leave the map and the Queen is told first, then the process is stopped,
then the provider is given back. A server that never becomes healthy is stopped and recorded as
dead, and a started server that later dies is found by ``sweep``. A server the Queen's promotion of
a device names is started the same way with ``only`` (the models to offer first) and ``deadline_s``
(how long she will wait), and ``load`` offers one more model of a server that already runs, within
the ceilings and without asking. Real Cells are borrowed and left exactly as found (codingrules
section 8.7): ``stop_all`` is how the Warden makes sure nothing it started outlives its lease.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Built by ``hivemind.wardens.warden.Warden`` once it holds its
    lease, started from ``Warden.start``, swept on its heartbeat, stopped by ``Warden.stop`` and by
    ``hivemind.wardens.ticks.control`` (a Queen-ordered eviction) and ``hivemind.wardens.ticks.
    nuc`` (a promotion or demotion). Calls into ``config``, ``launch``, ``offering``, ``report``,
    ``sources``, ``hivemind.cell``, ``hivemind.forage`` and waggle only.

Key invariants:
    - A source is on the Forage map and announced to the Queen only while its server's health probe
      has passed and its process runs; both are undone together, in reverse, on every stop.
    - No process this host started outlives ``stop_all``; each is stopped through the session that
      started it.
    - A Cell without ``can_host_model`` starts nothing; the reason is logged once, not per server.
    - The sum of loaded sources' VRAM never exceeds the Queen's ``model_vram_bytes`` when she has
      set ceilings.
    - A start that is cancelled leaves no process behind (``launch.bring_up``).

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - .claude/codingrules.md section 8.10 for "loading within the ceilings is autonomous".
    - hivemind.wardens.local_pool.hosting.report for what is sent and recorded.
"""

from __future__ import annotations

import types
from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.wardens.errors import UnknownServerError
from hivemind.wardens.local_pool.hosting.config import HostedCell, RunningServer, bounded
from hivemind.wardens.local_pool.hosting.launch import bring_up, shut_down
from hivemind.wardens.local_pool.hosting.offering import Offering
from hivemind.wardens.local_pool.hosting.report import ReportPort
from hivemind.wardens.local_pool.hosting.sources import narrow, plan_loads, vram_in_use
from waggle.ids import WardenId
from waggle.messages.forage import SourceRemovalCause

if TYPE_CHECKING:
    # Type-only: wardens.deps imports hosting.config, so a runtime import here would cycle.
    from hivemind.wardens.deps import WardenDeps

log = get_logger(__name__)

__all__ = ["ModelServerHost"]


class ModelServerHost:
    """Start, supervise and stop the model servers one Warden hosts on its Cell.

    Owns its running table in place (codingrules section 8.5): ``_running`` and ``_declined``
    change as servers start and stop; callers read them through ``running`` and ``declined``.
    """

    def __init__(self, deps: WardenDeps, warden_id: WardenId, place: HostedCell) -> None:
        """Build a host for one Warden's leased Cell; nothing starts until ``start``.

        Args:
            deps: The Warden's collaborators: ``servers``, ``map``, the provider door, its link to
                the Queen and its trail.
            warden_id: The Warden this host belongs to.
            place: Its Cell, session and a reader of the Queen's current ceilings.
        """
        self._deps = deps
        self._place = place
        self._running: dict[str, RunningServer] = {}
        self._declined: str | None = None
        port = ReportPort(
            queen_link=deps.queen_link,
            hop=deps.hop,
            trail=deps.trail,
            identity=deps.identity,
            clock=deps.clock,
            warden_id=warden_id,
            cell_id=place.cell.id,
        )
        self._offering = Offering(deps, place, port)

    @property
    def running(self) -> Mapping[str, RunningServer]:
        """A read-only snapshot of the servers currently running, by ``server_id``."""
        return types.MappingProxyType(dict(self._running))

    @property
    def declined(self) -> str | None:
        """Why this host started nothing (a Cell that cannot host a model), or None."""
        return self._declined

    def server_of(self, source_id: str) -> str | None:
        """Return the ``server_id`` currently offering ``source_id``, or None."""
        for server_id, server in self._running.items():
            if any(source.source_id == source_id for source in server.sources):
                return server_id
        return None

    async def start_all(self) -> None:
        """Start every declared server whose manifest row says ``load_on_start``."""
        for server_id, config in self._deps.servers.items():
            if config.load_on_start:
                await self.start(server_id)

    async def start(
        self, server_id: str, only: Collection[str] | None = None, deadline_s: float | None = None
    ) -> bool:
        """Start one declared server and offer its models; True when it is now running.

        Args:
            server_id: A key of ``WardenDeps.servers``.
            only: The source ids to offer, when the Queen named them (a promotion's first models);
                a name the ceilings or the server refuse is recorded as a Guard denial. None offers
                every source the ceilings allow, as the manifest's own start does.
            deadline_s: The longest she will wait for the server to answer; the manifest's own
                bound still applies when it is the shorter.

        Returns:
            True when the server runs (including one already running); False when nothing was
            started: the Cell cannot host a model, no source fits the ceilings, the process could
            not start, or its health probe never passed (then it was stopped and recorded dead).

        Raises:
            UnknownServerError: ``server_id`` is not declared in ``WardenDeps.servers``.
        """
        config = self._deps.servers.get(server_id)
        if config is None:
            raise UnknownServerError(server_id)
        if server_id in self._running:
            return True
        if not self._may_host():
            return False
        wanted, unknown = narrow(config.sources, only)
        plan = plan_loads(wanted, self._place.ceilings_of(), vram_in_use(self._running.values()))
        # Only what the Queen named is a request worth an audit row; a manifest that lists more
        # models than the ceilings allow is just logged.
        await self._offering.refuse(server_id, (*unknown, *plan.refused), record=only is not None)
        if not plan.load:
            return False  # Nothing this server may serve here: starting it would only waste VRAM.
        config = bounded(config, deadline_s)  # An order can hurry a start, never stretch one.
        server = await bring_up(self._deps, self._place, self._offering.port, config, plan.load)
        if server is None:
            return False  # bring_up already stopped what it started and recorded the failure.
        self._running[server_id] = server
        for source in server.sources:
            await self._offering.offer(server_id, source)
        return True

    async def load(self, server_id: str, source_id: str) -> bool:
        """Offer one more source of a running server, within the ceilings; True when it is on offer.

        Autonomous, and reported as a new source only (codingrules 8.10); a source outside the
        ceilings is refused, as a Guard denial on the trail.

        Args:
            server_id: A running server.
            source_id: The Forage map source to offer from it.

        Returns:
            True when the source is on offer now; False when the server is not running here or the
            source may not be loaded.
        """
        server = self._running.get(server_id)
        if server is None:
            return False  # Nothing runs to load it into: a promotion starts the server first.
        grown = await self._offering.load(server, source_id, vram_in_use(self._running.values()))
        if grown is None:
            return False
        self._running[server_id] = grown
        return True

    async def stop(self, server_id: str, cause: SourceRemovalCause) -> bool:
        """Stop one running server: withdraw its sources, kill its process, give its provider back.

        Args:
            server_id: The server to stop.
            cause: Why, as the Queen is told for each source (``SERVER_STOPPED``,
                ``SERVER_DIED``, ``LEASE_RELEASED``...).

        Returns:
            True when it was running; False when it was not (idempotent).
        """
        server = self._running.pop(server_id, None)
        if server is None:
            return False
        # Withdrawn before the kill: no call may be routed to a source whose process is going.
        for source in server.sources:
            await self._offering.withdraw(server_id, source, cause)
        await shut_down(self._deps, self._place, server)
        return True

    async def stop_all(self, cause: SourceRemovalCause) -> None:
        """Stop every running server, so nothing this Warden started outlives its lease."""
        for server_id in tuple(self._running):
            await self.stop(server_id, cause)

    async def unload(self, source_id: str, cause: SourceRemovalCause) -> bool:
        """Stop offering one source; stop its server too when it was the last one it offered.

        A generic server cannot unload a model on request, so this withdraws the source from the
        Hive's view (map and Queen); the model stays resident until its server stops.

        Args:
            source_id: The source to withdraw.
            cause: Why (``UNLOADED``, ``EVICTED``).

        Returns:
            True when the source was on a running server here; False otherwise.
        """
        server_id = self.server_of(source_id)
        if server_id is None:
            return False
        server = self._running[server_id]
        kept = tuple(s for s in server.sources if s.source_id != source_id)
        gone = next(s for s in server.sources if s.source_id == source_id)
        if not kept:
            return await self.stop(server_id, cause)
        self._running[server_id] = replace(server, sources=kept)
        await self._offering.withdraw(server_id, gone, cause)
        return True

    async def sweep(self) -> None:
        """Stop, as dead, every running server whose process has exited on its own."""
        for server_id, server in tuple(self._running.items()):
            if not await self._place.session.is_running(server.process):
                log.warning("wardens.hosting.died", server_id=server_id)
                await self.stop(server_id, SourceRemovalCause.SERVER_DIED)

    def _may_host(self) -> bool:
        """Return whether this Warden can host a model server here, declining once if not."""
        # The capability, never the Cell's kind (codingrules section 8.7): any Cell that reports
        # it may host, a Real Cell or a Virtual one.
        if not self._place.cell.capabilities.can_host_model:
            return self._decline("this Cell does not report can_host_model")
        if self._deps.register_provider is None:
            return self._decline("this Warden has no provider door to register a server with")
        return True

    def _decline(self, reason: str) -> bool:
        """Remember, and log once, why nothing starts; always False."""
        # Once, not per server: every declared server would repeat the same sentence.
        if self._declined is None:
            self._declined = reason
            log.warning("wardens.hosting.declined", reason=reason)
        return False
