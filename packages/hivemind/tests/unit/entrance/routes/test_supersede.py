"""Test hivemind.entrance.routes.isolation.supersede: the human's Supersedure, over real sockets.

Roadmap 13.2a, ADR-0065. ``POST /v1/hive/supersede`` is break-glass and loopback-only: without
step-up it answers ``403 step_up_required``, without the phrase ``break_glass_refused``, and a
Queen started without Supersedure refuses it; nothing starts in any of those cases. Stepped up,
with the phrase and a candidate that passes its test, the console's order freezes the Queen and
records `queen.supersedure_started` with the human's device as the actor.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from builders.entrance.serving import ServingRig, serving
from builders.supersedure import FakeCandidate, FakeSender, endpoint
from builders.swarm.parties import make_swarm_node

from hivemind.cell import AccessLevel
from hivemind.common.backup import BackupSource
from hivemind.pheromone import TrailQuery
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure import (
    STARTED_KIND,
    CandidateLink,
    Endpoint,
    ProbeReport,
    SupersedureWiring,
)
from hivemind.swarm import SwarmNode
from waggle.messages.swarm import RuntimeLevel

_PATH = "/v1/hive/supersede"
_NODE = "node_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PHRASE = "Supersede my Hive Stand"


def _wire(rig: ServingRig, tmp_path: Path) -> None:
    """Give the rig's Queen a candidate that passes its test and never becomes ready."""
    db = tmp_path / "hive.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.commit()
    source = BackupSource(db=db, hive_id=rig.deps.identity.hive_id)
    node = make_swarm_node(
        None, None, node_id=_NODE, level=RuntimeLevel.COLONIZED, access_level=AccessLevel.FULL
    )

    async def node_for(_node: str) -> SwarmNode | None:
        return node

    async def probe_for(_node: str) -> ProbeReport:
        return ProbeReport((), None, 0.0, reachable=True)

    async def link_for(_node: str) -> CandidateLink:
        return FakeCandidate([], ready_after=None)

    async def endpoint_for(node_id: str) -> Endpoint:
        return endpoint(node_id, 9001)

    async def backup() -> BackupSource:
        return source

    rig.deps.supersedure.wiring = SupersedureWiring(
        own=endpoint(rig.deps.identity.node_id, 9000),
        node_for=node_for,
        link_for=link_for,
        probe_for=probe_for,
        sender=FakeSender([]),
        source=backup,
        endpoint_for=endpoint_for,
        runtime_version="1.0.0",
        workdir=tmp_path / "work",
    )


async def test_a_supersedure_needs_step_up_the_phrase_and_a_wired_hive() -> None:
    async with serving() as rig:
        console, session = await rig.console_session()
        body = {"node_id": _NODE, "phrase": _PHRASE}

        unstepped = await console.call(session, "POST", _PATH, body)
        await console.step_up(session)
        wrong = await console.call(session, "POST", _PATH, {**body, "phrase": "move it"})
        unwired = await console.call(session, "POST", _PATH, body)
        started = await rig.deps.trail.query(TrailQuery(kind=STARTED_KIND))

    assert unstepped.status_code == 403 and unstepped.json()["error"].endswith("step_up_required")
    assert wrong.status_code == 403 and wrong.json()["error"].endswith("break_glass_refused")
    assert unwired.status_code == 403 and unwired.json()["error"].endswith("supersedure_refused")
    assert started == ()


async def test_after_step_up_the_phrase_freezes_the_queen_and_records_the_start(
    tmp_path: Path,
) -> None:
    async with serving() as rig:
        _wire(rig, tmp_path)
        console, session = await rig.console_session()
        checks = await console.call(session, "GET", f"{_PATH}/{_NODE}/preflight", None)
        await console.step_up(session)
        moved = await console.call(session, "POST", _PATH, {"node_id": _NODE, "phrase": _PHRASE})
        mode = rig.deps.cluster_state.mode
        started = await rig.deps.trail.query(TrailQuery(kind=STARTED_KIND))
        task = rig.deps.supersedure.task
        assert task is not None
        task.cancel()  # Its candidate never becomes ready; the test ends before the window.

    assert checks.status_code == 200 and checks.json()["passed"], checks.text
    assert moved.status_code == 200, moved.text
    [event] = started
    assert moved.json()["started_id"] == event.id
    assert (event.actor, event.payload["candidate_node_id"]) == (session.key.device_id, _NODE)
    assert mode is QueenMode.SUPERSEDING
