"""Give the human the Supersedure lever on the running Queen, and give the driver her levers.

Roadmap 13.2a: `hive supersede <node>` is human-initiated on loopback with step-up and the typed
phrase, never an autopilot rule or an awake decision. It arrives from the Hive Entrance's
loopback-only route through `hivemind.entrance.gate.QueenDoor` and ends here. `SupersedureDoor`
is a mixin `hivemind.queen.queen.Queen` inherits: `supersedure_preflight` runs the candidate test
for `hive doctor --supersedure`, and `supersede` passes the Guard's `supersedure` point as the
operator (human-only: ADR-0065 put it under the human-only floor), refuses a failed preflight, a
Supersedure already under way or a Queen not RUNNING or CLUSTERED, then freezes her, records the
start and leaves the rest (`.run.finish`) running on her own loop. `QueenLevers` is the
`QueenControls` the driver freezes, stands down and wakes her through.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by the Entrance through
    `QueenDoor`. Calls into `hivemind.guard`, `hivemind.queen.cluster`, `hivemind.queen.errors`,
    `hivemind.queen.state`, `hivemind.common.backup` and this package's own modules; `QueenDeps`
    and `WardenLink` only for their types.

Key invariants:
    - Nothing is frozen before the point, the preflight and the mode check have all passed.
    - At most one Supersedure runs per process (`SupersedureBook.task`).

See Also:
    - hivemind.entrance.routes.hive for the route, its step-up and its phrase.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.common.backup import BackupSource
from hivemind.common.logging import get_logger
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
    role_set,
)
from hivemind.guard.policy import OPERATOR_ID, OPERATOR_ROLE
from hivemind.queen.cluster.protocol import cluster, resume
from hivemind.queen.errors import SupersedureRefusedError
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure.handover import Handover
from hivemind.queen.supersedure.ports import SupersedureWiring
from hivemind.queen.supersedure.preflight import (
    CheckOutcome,
    HiveNeeds,
    PreflightReport,
    check_candidate,
    facts_for,
)
from hivemind.queen.supersedure.record import Recorder
from hivemind.queen.supersedure.rollback import Rollback
from hivemind.queen.supersedure.run import SupersedurePlan, SupersedurePorts, begin, finish
from waggle.ids import DeviceId, WardenId

if TYPE_CHECKING:
    from hivemind.queen.deps import QueenDeps, WardenLink

FREEZE_KEY = "supersedure"  # Held in the clustered set for the move's whole length (QueenLevers).
FREEZE_CAUSE = "supersedure"  # The `cause` the freeze's `queen.clustered` events carry.
_MOVABLE = (QueenMode.RUNNING, QueenMode.CLUSTERED)

log = get_logger(__name__)

__all__ = ["FREEZE_KEY", "QueenLevers", "SupersedureDoor", "SupersedureStarted"]


@dataclass(frozen=True, slots=True)
class SupersedureStarted:
    """What the human's order started: the record the trail now holds."""

    started_id: str
    candidate_node_id: str
    sequence: int
    grace_until: datetime


class QueenLevers:
    """The old Queen's `QueenControls`: cluster everything, then the Supersedure edges."""

    def __init__(self, deps: QueenDeps, wardens: Callable[[], Sequence[WardenLink]]) -> None:
        """Act on `deps.cluster_state` through Clustering, with `wardens` read at each call."""
        self._deps = deps
        self._wardens = wardens

    async def freeze(self) -> None:
        """Cluster every bound provider and the freeze key, then move to SUPERSEDING."""
        deps, state = self._deps, self._deps.cluster_state
        for provider in sorted({binding.provider for binding in deps.bindings}):
            await cluster(provider, FREEZE_CAUSE, deps, state, self._wardens())
        # The key keeps her CLUSTERED (and so able to move to SUPERSEDING) on a Hive whose slots
        # name no provider at all; `wake` removes it with the rest.
        state.mark_clustered(FREEZE_KEY)
        state.begin_supersedure()

    async def stand_down(self) -> None:
        """Move SUPERSEDING -> SUPERSEDED; her tick then ends her loop."""
        self._deps.cluster_state.finish_supersedure()

    async def wake(self) -> None:
        """Back to CLUSTERED, then resume every provider the freeze (or an outage) clustered."""
        state = self._deps.cluster_state
        state.abort_supersedure()
        state.mark_resumed(FREEZE_KEY)
        for provider in sorted(state.clustered_providers):
            await resume(provider, self._deps, state, self._wardens())


class SupersedureDoor:
    """The human's Supersedure lever, as methods of the running Queen.

    Reads `self._deps` and `self._wardens`, both set by `Queen.__init__`; this class is never
    instantiated on its own.
    """

    _deps: QueenDeps
    _wardens: dict[WardenId, WardenLink]

    async def supersedure_preflight(self, node_id: str) -> PreflightReport:
        """Run the candidate test for `node_id` (`hive doctor --supersedure`).

        Raises:
            SupersedureRefusedError: This Hive has no Supersedure wiring.
        """
        wiring = self._wiring(node_id)
        source = await wiring.source()
        needs = HiveNeeds(
            runtime_version=wiring.runtime_version,
            stores_bytes=_stores_bytes(source),
            secret_names=source.secret_names,
            identity_key=source.identity_key,
            reserve=self._deps.reserve,
        )
        facts = facts_for(node_id, await wiring.node_for(node_id), await wiring.probe_for(node_id))
        return check_candidate(facts, needs)

    async def supersede(self, node_id: str, device_id: DeviceId) -> SupersedureStarted:
        """Move the Hive Stand onto `node_id`, on the human's order (module docstring).

        Returns:
            The start as recorded; the copy and the handover go on on her loop.

        Raises:
            SupersedureRefusedError: The point refused, the preflight failed, one is under way,
                she is not RUNNING or CLUSTERED, or this Hive has no Supersedure wiring.
        """
        deps, book = self._deps, self._deps.supersedure
        wiring = self._wiring(node_id)
        if not (await deps.enforcer.check(_request(deps))).allowed:
            raise SupersedureRefusedError(node_id, "the supersedure point refused (guard.denied)")
        if book.task is not None and not book.task.done():
            raise SupersedureRefusedError(node_id, "a Supersedure is already under way")
        if deps.cluster_state.mode not in _MOVABLE:
            raise SupersedureRefusedError(node_id, f"the Queen is {deps.cluster_state.mode.value}")
        report = await self.supersedure_preflight(node_id)
        if not report.passed:
            failed = ", ".join(c.name for c in report.checks if c.outcome is CheckOutcome.FAILED)
            raise SupersedureRefusedError(node_id, f"preflight failed: {failed}")
        plan = SupersedurePlan(
            candidate=await wiring.endpoint_for(node_id),
            own=wiring.own,
            actor=str(device_id),
            grace_s=wiring.grace_s,
            poll_s=wiring.poll_s,
            workdir=wiring.workdir,
        )
        recorder = Recorder(deps.trail, deps.identity.hive_id, deps.identity.node_id, deps.clock)
        levers = QueenLevers(deps, lambda: tuple(self._wardens.values()))
        ports = SupersedurePorts(recorder, levers, await wiring.link_for(node_id), wiring.sender)
        record = await begin(plan, ports)
        source = await wiring.source()
        book.task = asyncio.ensure_future(finish(record, plan, ports, source))
        book.task.add_done_callback(lambda done: _note(book.outcomes, done))
        return SupersedureStarted(
            record.started_id, record.candidate_node_id, record.sequence, record.grace_until
        )

    def _wiring(self, node_id: str) -> SupersedureWiring:
        """This Hive's Supersedure wiring, or a refusal naming why there is none."""
        wiring = self._deps.supersedure.wiring
        if wiring is None:
            raise SupersedureRefusedError(node_id, "this Hive was started without Supersedure")
        return wiring


def _note(outcomes: list[Handover | Rollback], done: asyncio.Task[Handover | Rollback]) -> None:
    """Keep a finished Supersedure's outcome, or log the error that ended it."""
    if done.cancelled():
        return
    error = done.exception()
    if error is not None:
        log.error("queen.supersedure_crashed", error=str(error))
        return
    outcomes.append(done.result())


def _stores_bytes(source: BackupSource) -> int:
    """The database plus every tree's files: what the candidate's disk must hold."""
    total = source.db.stat().st_size if source.db.is_file() else 0
    for root in source.trees.values():
        if root.is_dir():
            # Latency: one walk of the Basket's blob directory; a backup reads it all anyway.
            total += sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return total


def _request(deps: QueenDeps) -> PolicyRequest:
    """Build the `supersedure` check: the human, needing `supersede`, holding the operator's set."""
    return PolicyRequest(
        principal=PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role=OPERATOR_ROLE),
        point=EnforcementPoint.SUPERSEDURE,
        needed=Capability(family=CapabilityFamily.SUPERSEDE),
        held=role_set(deps.enforcer.policy, OPERATOR_ROLE),
        context=PolicyContext(),
    )
