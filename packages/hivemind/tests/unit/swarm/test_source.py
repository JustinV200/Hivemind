"""Tests for hivemind.swarm.source: an enrolled device as a source of one Real Cell.

Fits into the Hive:
    Mirrors src/hivemind/swarm/source.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The RealCellSource contract clauses are also run over SwarmSource in
    tests/contracts/test_real_cell_source_contract.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.source for the module under test.
    - docs/waggle/gateway-contract.md sections 9.1 and 13 for the lease messages.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from builders.cells import make_lease_request
from builders.swarm import (
    DeviceProcess,
    DeviceScript,
    LinkRig,
    RecordingGuard,
    ReleaseScript,
    make_heartbeat,
    make_link_rig,
    make_source,
    make_swarm_node,
)

from hivemind.cell import AccessLevel, CellKind, CombShieldLevel
from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.errors import DeviceRequestError, GatewayUnavailableError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import SWARM_SOURCE
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.source import SwarmSource
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import LeaseId, new_lease_id, new_task_id, new_warden_id
from waggle.messages.cell.leases import (
    CellRequest,
    CellTeardownRequest,
    LeaseOpened,
    LeaseReleased,
)
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.labels import AccessLevel as WireAccess
from waggle.messages.labels import Urgency

RigFactory = Callable[[DeviceScript], Awaitable[LinkRig]]


def _request(rig: LinkRig, **overrides: object) -> LeaseRequest:
    """A lease request for the rig's Cell by its Warden."""
    return make_lease_request(
        rig.clock, cell_id=rig.node.cell_id, holder=rig.node.warden_id, **overrides
    )


async def _lease(
    rig: LinkRig, source: SwarmSource | None = None, **overrides: object
) -> RealCellLease:
    """Lease the rig's device, through `source` or a fresh one."""
    return await (source or make_source(rig)).lease(_request(rig, **overrides))


class _RefusingTrail(MemoryPheromoneTrail):
    """A trail that refuses the next record of one kind, as a momentarily locked SQLite file."""

    def __init__(self, rig: LinkRig, refused_kind: str) -> None:
        super().__init__(rig.clock)
        self._refused_kind: str | None = refused_kind

    async def record(self, event: PheromoneEvent) -> None:
        """Fail the first record of the refused kind like a busy database; record the rest."""
        if event.kind == self._refused_kind:
            self._refused_kind = None
            raise sqlite3.OperationalError("database is locked")
        await super().record(event)


def _source_with_refusing_trail(rig: LinkRig, refused_kind: str) -> SwarmSource:
    """A source whose recorder writes to a trail that refuses the next `refused_kind`."""
    recorder = SwarmRecorder(_RefusingTrail(rig, refused_kind), rig.clock, rig.recorder.identity)
    source = SwarmSource(rig.node, rig.hub, recorder, RecordingGuard())
    rig.hub.subscribe(source)  # So it hears a dead-man release, as the composition root wires it.
    return source


def _teardowns_sent(rig: LinkRig) -> list[CellTeardownRequest]:
    """Every teardown the device has been asked for, in order."""
    return [
        e.payload for e in rig.device.state.received if isinstance(e.payload, CellTeardownRequest)
    ]


def _released_frame(rig: LinkRig, lease_id: LeaseId) -> Envelope:
    """The frame a device sends, unprompted, when it has released `lease_id` on its own."""
    payload = LeaseReleased(
        lease_id=lease_id,
        cell_id=rig.node.cell_id,
        holder=rig.node.warden_id,
        cause=ReleaseCause.DEAD_MAN,
        is_restored=True,
        killed_processes=0,
        residual_paths=(),
        reason="Dead-man.",
    )
    hop = Hop(sender=rig.node.device_id, recipient=rig.hive.hive_id, node_id=rig.node.node_id)
    return wrap(payload, hop, clock=rig.clock)


async def test_the_source_is_named_swarm_and_lists_the_devices_real_cell(rig: LinkRig) -> None:
    source = make_source(rig)

    (cell,) = await source.cells()

    assert source.name == SWARM_SOURCE == "swarm"
    assert (cell.id, cell.kind, cell.source) == (rig.node.cell_id, CellKind.REAL, "swarm")
    assert cell.access_level is rig.node.access_level


async def test_a_revoked_device_has_no_cell(rig: LinkRig) -> None:
    revoked = rig.node.model_copy(
        update={"status": NodeStatus.REVOKED, "revoked_at": rig.clock.now()}
    )
    rig.hub.update_node(revoked)

    assert await make_source(rig).cells() == ()


async def test_cells_reads_the_hubs_freshest_record_of_the_device(rig: LinkRig) -> None:
    source = make_source(rig)
    rig.hub.update_node(rig.node.model_copy(update={"label": "kitchen laptop"}))

    (cell,) = await source.cells()

    assert cell.name == "kitchen laptop"


async def test_a_lease_asks_the_device_as_the_queen_for_its_warden(rig: LinkRig) -> None:
    task = new_task_id(rig.clock)
    source = SwarmSource(
        rig.node, rig.hub, rig.recorder, RecordingGuard(), default_scratch_quota_bytes=4096
    )

    lease = await source.lease(_request(rig, task_id=task))

    (sent,) = [e for e in rig.device.state.received if isinstance(e.payload, CellRequest)]
    assert isinstance(sent.payload, CellRequest)
    assert sent.sender == rig.hive.hive_id
    assert (sent.payload.cell_id, sent.payload.holder, sent.payload.task_id) == (
        rig.node.cell_id,
        rig.node.warden_id,
        task,
    )
    assert (sent.payload.access_level, sent.payload.scratch_quota_bytes) == (
        WireAccess.SCRATCH,
        4096,
    )
    (device_lease,) = rig.device.state.leases.values()
    assert lease.id == device_lease.lease_id
    assert lease.scratch_root == Path(device_lease.scratch_root)
    assert (lease.state, lease.task_id) == (LeaseState.OPEN, task)


def _asked_quota(rig: LinkRig) -> int | None:
    """The scratch quota the device's one `cell.request` carried."""
    (sent,) = [e for e in rig.device.state.received if isinstance(e.payload, CellRequest)]
    assert isinstance(sent.payload, CellRequest)
    return sent.payload.scratch_quota_bytes


async def test_the_quota_the_queen_set_is_what_the_device_is_asked_for(rig: LinkRig) -> None:
    # The holder's ceilings ride on the request; the source's own figure is only a default.
    source = SwarmSource(
        rig.node, rig.hub, rig.recorder, RecordingGuard(), default_scratch_quota_bytes=4096
    )

    lease = await source.lease(_request(rig, scratch_quota_bytes=1024))

    assert _asked_quota(rig) == 1024
    (device_lease,) = rig.device.state.leases.values()
    assert device_lease.quota_bytes == 1024  # The device holds the lease to it.
    assert lease.state is LeaseState.OPEN


async def test_a_request_that_names_no_quota_gets_the_sources_default(rig: LinkRig) -> None:
    source = SwarmSource(
        rig.node, rig.hub, rig.recorder, RecordingGuard(), default_scratch_quota_bytes=4096
    )

    await source.lease(_request(rig))

    assert _asked_quota(rig) == 4096


async def test_with_no_figure_anywhere_the_device_is_left_to_its_own_default(rig: LinkRig) -> None:
    await make_source(rig).lease(_request(rig))

    assert _asked_quota(rig) is None


async def test_the_access_level_is_what_the_device_granted(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript(node_access=WireAccess.READ_ONLY))

    lease = await _lease(rig, access_level=AccessLevel.FULL)

    assert lease.access_level is AccessLevel.READ_ONLY


async def test_the_tier_is_the_registrys_not_the_devices_echo() -> None:
    rig = await make_link_rig(node_overrides={"comb_shield": CombShieldLevel.PROPOLIS})
    try:
        lease = await _lease(rig)
    finally:
        await rig.close()

    assert lease.comb_shield is CombShieldLevel.PROPOLIS


async def test_allowed_paths_of_the_request_are_carried_on_the_lease(rig: LinkRig) -> None:
    lease = await _lease(rig, allowed_paths=(Path("/srv/work"),))

    assert lease.allowed_paths == (Path("/srv/work"),)


async def test_the_lease_judges_a_path_by_the_devices_own_rule_not_the_hive_stands_disk(
    rig: LinkRig,
) -> None:
    lease = await _lease(rig)
    root = lease.scratch_root  # A path on the device that the Hive Stand's disk knows nothing of.

    assert lease.is_path_allowed(Path("notes.txt"))  # Relative: under the device's scratch.
    assert lease.is_path_allowed(root / "a" / ".." / "b.txt")
    assert not lease.is_path_allowed(root / ".." / "elsewhere")
    lease.note_allowed_path(Path("/srv/work/../work"))
    assert lease.allowed_paths == (Path("/srv/work"),)  # Stored as the device spells it.


async def test_a_windows_devices_lease_is_judged_in_windows_spelling(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript(scratch_base="C:\\Pollen"))

    lease = await _lease(rig)

    assert lease.is_path_allowed(Path(f"{lease.scratch_root}\\sub\\..\\f.txt"))
    assert lease.is_path_allowed(Path("f.txt"))
    assert not lease.is_path_allowed(Path("C:\\Pollen\\..\\Other\\f.txt"))


async def test_leasing_records_cell_leased_and_releasing_records_cell_released(
    rig: LinkRig,
) -> None:
    lease = await _lease(rig)

    await lease.release()

    events = await rig.trail.query(TrailQuery(subject_id=rig.node.cell_id))
    assert [event.kind for event in events] == ["cell.leased", "cell.released"]


async def test_a_lease_whose_record_fails_is_given_back_to_the_device_and_the_error_raised(
    rig: LinkRig,
) -> None:
    # The device opened the lease before `cell.leased` was written; a trail that refuses the
    # write must not leave the device holding a lease nothing on the Hive Stand references.
    source = _source_with_refusing_trail(rig, "cell.leased")

    with pytest.raises(sqlite3.OperationalError):
        await _lease(rig, source)

    (teardown,) = _teardowns_sent(rig)
    assert teardown.cell_id == rig.node.cell_id
    assert rig.device.state.leases == {}  # Left exactly as found.
    second = await _lease(rig, source)  # The source is free for the next request...
    assert second.state is LeaseState.OPEN  # ...which the device opens afresh.


async def test_a_lease_whose_record_fails_and_cannot_be_given_back_stays_held_as_orphaned(
    rigs: RigFactory,
) -> None:
    rig = await rigs(DeviceScript(refusals={CellTeardownRequest: "pollen.unsupported"}))
    source = _source_with_refusing_trail(rig, "cell.leased")

    with pytest.raises(sqlite3.OperationalError):
        await _lease(rig, source)

    # The tenancy still names the lease, so a second request is refused rather than opening a
    # second lease the device would refuse, and the device's own dead-man report can finish it.
    with pytest.raises(LeaseRefusedError, match="already leased"):
        await _lease(rig, source)
    (held,) = rig.device.state.leases.values()
    rig.heard.clear()
    await rig.device.release_unprompted(held.lease_id)
    await asyncio.wait_for(rig.heard.wait(), 5.0)
    assert await _lease(rig, source)  # Released by the device's word; the source is free again.


async def test_a_release_asks_the_device_gracefully_as_the_queen_and_reports_its_answer(
    rigs: RigFactory,
) -> None:
    script = DeviceScript(release=ReleaseScript(is_restored=False, residual_paths=("/data/x",)))
    rig = await rigs(script)
    lease = await _lease(rig)

    report = await lease.release()

    (sent,) = [e for e in rig.device.state.received if isinstance(e.payload, CellTeardownRequest)]
    assert isinstance(sent.payload, CellTeardownRequest)
    assert sent.sender == rig.hive.hive_id
    assert (sent.payload.lease_id, sent.payload.urgency, sent.payload.cause) == (
        lease.id,
        Urgency.GRACEFUL,
        ReleaseCause.COMPLETED,
    )
    assert (report.is_restored, report.residual_paths) == (False, (Path("/data/x"),))
    assert lease.state is LeaseState.RELEASED


async def test_a_release_reports_the_processes_the_device_killed(rig: LinkRig) -> None:
    lease = await _lease(rig)
    rig.device.state.processes[1] = DeviceProcess(1, lease.id, ("runs",), None)

    report = await lease.release()

    assert report.killed_processes == 1


async def test_a_second_lease_is_refused_until_the_first_is_released(rig: LinkRig) -> None:
    source = make_source(rig)
    first = await _lease(rig, source)

    with pytest.raises(LeaseRefusedError):
        await _lease(rig, source)
    await first.release()

    assert (await _lease(rig, source)).state is LeaseState.OPEN


async def test_a_request_for_another_cell_is_refused_without_asking_the_device(
    rig: LinkRig,
) -> None:
    other = make_lease_request(rig.clock, holder=rig.node.warden_id)

    with pytest.raises(LeaseRefusedError):
        await make_source(rig).lease(other)

    assert not [e for e in rig.device.state.received if isinstance(e.payload, CellRequest)]


async def test_a_device_that_is_not_connected_refuses_the_lease(rig: LinkRig) -> None:
    rig.hub.detach(rig.node.device_id, rig.link)

    with pytest.raises(LeaseRefusedError):
        await _lease(rig)


async def test_a_revoked_device_refuses_the_lease(rig: LinkRig) -> None:
    rig.hub.update_node(
        rig.node.model_copy(update={"status": NodeStatus.REVOKED, "revoked_at": rig.clock.now()})
    )

    with pytest.raises(LeaseRefusedError):
        await _lease(rig)


async def test_a_device_that_refuses_the_holder_refuses_the_lease(rigs: RigFactory) -> None:
    rig = await rigs(DeviceScript(accept_any_holder=False))

    with pytest.raises(LeaseRefusedError) as raised:
        await make_source(rig).lease(make_lease_request(rig.clock, cell_id=rig.node.cell_id))

    assert "pollen.lease_refused" in str(raised.value)


async def test_a_device_that_stays_silent_refuses_the_lease_after_the_deadline(
    rigs: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hivemind.swarm.source.LEASE_REPLY_TIMEOUT_S", 0.2)
    rig = await rigs(DeviceScript(silent={CellRequest}))

    with pytest.raises(LeaseRefusedError):
        await _lease(rig)


async def test_a_device_that_answers_about_another_warden_refuses_the_lease(rig: LinkRig) -> None:
    async def another_holder(request: Envelope) -> None:
        await rig.device.send_raw(
            LeaseOpened(
                lease_id=new_lease_id(rig.clock),
                cell_id=rig.node.cell_id,
                holder=new_warden_id(rig.clock),
                task_id=None,
                access_level=WireAccess.SCRATCH,
                comb_shield=rig.device.script.comb_shield,
                scratch_root="/x",
                allowed_paths=(),
                dead_man_s=60.0,
                reason="Wrong.",
            ),
            request.id,
        )

    rig.device.override(CellRequest, another_holder)

    with pytest.raises(LeaseRefusedError):
        await _lease(rig)


async def test_a_release_with_the_device_away_raises_and_leaves_the_lease_orphaned(
    rig: LinkRig,
) -> None:
    lease = await _lease(rig)
    rig.hub.detach(rig.node.device_id, rig.link)

    with pytest.raises(GatewayUnavailableError):
        await lease.release()

    assert lease.state is LeaseState.ORPHANED


async def test_a_release_the_device_no_longer_recognises_is_reported_unconfirmed(
    rig: LinkRig,
) -> None:
    lease = await _lease(rig)
    rig.device.state.leases.clear()

    report = await lease.release()

    assert report.is_restored is False
    assert lease.state is LeaseState.RELEASED


async def test_any_other_refusal_of_a_release_propagates_and_orphans_the_lease(
    rigs: RigFactory,
) -> None:
    rig = await rigs(DeviceScript(refusals={CellTeardownRequest: "pollen.unsupported"}))
    lease = await _lease(rig)

    with pytest.raises(DeviceRequestError):
        await lease.release()

    assert lease.state is LeaseState.ORPHANED


async def test_a_device_that_answers_a_release_with_something_else_orphans_the_lease(
    rig: LinkRig,
) -> None:
    lease = await _lease(rig)

    async def wrong_answer(request: Envelope) -> None:
        await rig.device.send_raw(make_heartbeat(rig.party), request.id)

    rig.device.override(CellTeardownRequest, wrong_answer)

    with pytest.raises(DeviceRequestError):
        await lease.release()

    assert lease.state is LeaseState.ORPHANED


async def test_a_dead_man_release_the_device_reports_unprompted_releases_the_lease(
    rig: LinkRig,
) -> None:
    lease = await _lease(rig)
    rig.heard.clear()

    await rig.device.release_unprompted(lease.id)
    await asyncio.wait_for(rig.heard.wait(), 5.0)

    assert lease.state is LeaseState.RELEASED
    events = await rig.trail.query(TrailQuery(subject_id=rig.node.cell_id))
    assert [event.kind for event in events] == ["cell.leased", "cell.released"]
    sent_before = len(rig.device.state.received)
    await lease.release()  # Idempotent: nothing more goes to the device.
    assert len(rig.device.state.received) == sent_before


async def test_a_dead_man_report_is_the_devices_own_not_a_guess(rigs: RigFactory) -> None:
    script = DeviceScript(release=ReleaseScript(is_restored=False, residual_paths=("/r",)))
    rig = await rigs(script)
    lease = await _lease(rig)
    rig.heard.clear()

    await rig.device.release_unprompted(lease.id)
    await asyncio.wait_for(rig.heard.wait(), 5.0)

    report = await lease.release()
    assert (report.is_restored, report.residual_paths) == (False, (Path("/r"),))


async def test_frames_that_are_not_this_leases_release_are_ignored(rig: LinkRig) -> None:
    source = make_source(rig)
    lease = await _lease(rig, source)
    other_device = make_swarm_node()

    await source.on_frame(rig.node, _released_frame(rig, new_lease_id(rig.clock)))  # Another lease.
    await source.on_frame(other_device, _released_frame(rig, lease.id))  # Another device.
    await rig.device.heartbeat()  # Not a release at all.
    await asyncio.wait_for(rig.heard.wait(), 5.0)

    assert lease.state is LeaseState.OPEN


async def test_a_release_report_for_a_lease_already_released_is_ignored(rig: LinkRig) -> None:
    source = make_source(rig)
    lease = await _lease(rig, source)
    await lease.release()

    await source.on_frame(rig.node, _released_frame(rig, lease.id))

    assert lease.state is LeaseState.RELEASED


async def test_a_release_report_before_any_lease_is_ignored(rig: LinkRig) -> None:
    await make_source(rig).on_frame(rig.node, _released_frame(rig, new_lease_id(rig.clock)))


async def test_a_session_cannot_open_when_the_device_is_not_connected(rig: LinkRig) -> None:
    source = make_source(rig)
    lease = await _lease(rig, source)
    rig.hub.detach(rig.node.device_id, rig.link)

    with pytest.raises(GatewayUnavailableError):
        await source.open_session(lease)


async def test_the_source_ignores_a_device_coming_and_going(rig: LinkRig) -> None:
    source = make_source(rig)

    await source.on_online(rig.node, rig.link)
    await source.on_offline(rig.node, "link_lost")
