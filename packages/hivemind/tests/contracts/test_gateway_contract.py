"""Gateway conformance suite, part one: enrolment, the link, housekeeping, leases and capabilities.

The suite checks a gateway (the Pollen Packet of this workspace, or any program that speaks the
gateway contract in `docs/waggle/gateway-contract.md`) from the outside, the way the Hive Stand
meets it. It starts the real swarm listener on a loopback socket, mints invites, starts the
gateway as a subprocess, lets it enrol, and then sends it signed requests as the Queen and the
Warden and asserts what it sends back and what it leaves on the device. It never imports the
gateway. The suite is this module and its siblings `test_gateway_contract_programs.py` (sessions,
exec and background processes), `test_gateway_contract_files.py` (files and observation) and
`test_gateway_contract_resilience.py` (replays, forgeries, reconnection, the outbox, the dead-man
switch and QueenMoved); the rig they share is the package `contracts/gateway/`.

Running it against another gateway:
    Set `HIVEMIND_GATEWAY_COMMAND` to a command template and run the four modules, e.g.
    `HIVEMIND_GATEWAY_COMMAND='mygateway --invite {invite} --state {data_dir}' uv run pytest
    packages/hivemind/tests/contracts/test_gateway_contract*.py`. Without the variable the suite
    runs the Pollen Packet: `{python} -m pollen enrol --data-dir {data_dir} {invite} && {python}
    -m pollen run --data-dir {data_dir}`. A template is a command line, split into words as a
    POSIX shell splits it and run without a shell, with these placeholders filled in its words:

    - `{invite}`: the `pollen1.` invite string the gateway enrols with (docs/waggle/enrolment.md
      section 3.3). A secret: the gateway must not print it.
    - `{data_dir}`: an empty directory, new for each gateway started, for its key, state, leases
      and outbox. The suite never reads it.
    - `{python}`: the interpreter running the suite, for gateways written in Python.

    A word that is exactly `&&` ends a step: every step but the last runs to completion and must
    exit 0 (a gateway that enrols with one command and runs with another puts the enrol first),
    and the last step is the gateway, which runs until the suite ends it with SIGTERM. A gateway
    that enrols and runs in one command is a one-step template. The gateway runs on the machine
    that runs the suite: where a clause is about the device's disk or process table, the suite
    looks at this machine's. The suite also assumes the gateway's clock agrees with the Hive
    Stand's (it is the same machine), and that the invite names a loopback `ws://` address.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises sections 4 to 10 of the
    gateway contract against `hivemind.swarm.gateway` (the listener) and `packages/pollen` (the
    reference gateway), through the rig in `contracts.gateway`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/gateway-contract.md, the contract these tests state, and section 2 for how to run
      the suite.
    - docs/waggle/enrolment.md for the ceremony the first tests walk.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import psutil
import pytest
import pytest_asyncio
from builders.swarm.live import LiveHive, LiveHiveOptions, start_live_hive
from contracts.gateway import (
    Gateway,
    GatewayStartError,
    alive,
    exists,
    is_directory,
    python_argv,
    read_file,
    refused,
    running_gateway,
    start_gateway,
    write_file,
)

from hivemind.cell import AccessLevel
from hivemind.swarm.enrolment import node_update_for
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError
from waggle.clock import SystemClock
from waggle.envelope import PROTOCOL_MINOR
from waggle.ids import new_cell_id, new_warden_id
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.control import Ping, Pong
from waggle.messages.forage.capacity import CapacityTrigger
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.labels import AccessLevel as WireAccess
from waggle.messages.labels import Urgency
from waggle.messages.swarm import (
    Colonize,
    ColonizeAction,
    Colonized,
    DeviceHeartbeat,
    RuntimeLevel,
)

# One event loop for the whole module, so the Hive Stand and the gateways started by the module's
# fixtures outlive a test and a gateway is not restarted (about a second each) for every clause.
pytestmark = pytest.mark.asyncio(loop_scope="module")

HEARTBEAT_S = 0.5  # The beat every gateway is told to keep, so a test sees several in a second.
_SLEEP_FOREVER = "import time; time.sleep(600)"  # A program that runs until it is killed.
DENIED = "pollen.capability_denied"  # What a request the gateway's grants do not cover draws.


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def hive() -> AsyncIterator[LiveHive]:
    """A Hive Stand with the real listener, telling its devices to beat twice a second."""
    stand = await start_live_hive(LiveHiveOptions(heartbeat_interval_s=HEARTBEAT_S))
    yield stand
    await stand.stop()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def gateway(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway enrolled with a FULL invite and online."""
    async with running_gateway(hive, tmp_path_factory.mktemp("full"), AccessLevel.FULL) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def read_only(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway whose invite is capped at READ_ONLY."""
    async with running_gateway(hive, tmp_path_factory.mktemp("ro"), AccessLevel.READ_ONLY) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scratch(
    hive: LiveHive, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[Gateway]:
    """A gateway whose invite is capped at SCRATCH."""
    async with running_gateway(hive, tmp_path_factory.mktemp("sc"), AccessLevel.SCRATCH) as g:
        yield g


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def terms(hive: LiveHive, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Gateway]:
    """A FULL gateway reserved for the tests that change its terms; each puts them back."""
    async with running_gateway(hive, tmp_path_factory.mktemp("terms"), AccessLevel.FULL) as g:
        yield g


# ── Enrolment (section 4, enrolment.md) ─────────────────────────────────────────


async def test_a_gateway_enrols_and_the_hive_knows_it_by_its_ids_and_key(gateway: Gateway) -> None:
    node = await gateway.hive.node(gateway.node.device_id)

    assert str(node.device_id).startswith("device_") and str(node.node_id).startswith("node_")
    assert len(node.public_key_hex) == 64
    assert node.level is RuntimeLevel.GATEWAY
    # It asked for the most and the invite capped nothing, so it holds FULL and the Meadow tier.
    assert node.access_level is AccessLevel.FULL
    assert {"swarm.invited", "swarm.enrolled"} <= set(await gateway.hive.kinds())


async def test_a_spent_invite_ends_a_second_enrolment_attempt_and_creates_no_node(
    gateway: Gateway, tmp_path: Path
) -> None:
    nodes = len(await gateway.hive.registry.nodes())

    with pytest.raises(GatewayStartError):
        await start_gateway(gateway.hive, tmp_path, invite=gateway.minted)

    assert len(await gateway.hive.registry.nodes()) == nodes
    assert "swarm.enrol_refused" in await gateway.hive.kinds()


async def test_the_gateway_never_prints_the_invite_or_its_token(gateway: Gateway) -> None:
    output = gateway.process.output()

    assert gateway.invite not in output
    assert gateway.token not in output


# ── The link (section 5) ──────────────────────────────────────────────────────────


async def test_a_connection_opens_with_a_heartbeat_then_a_capacity_report(gateway: Gateway) -> None:
    frames = [
        f
        for f in gateway.hive.log.frames
        if f.sender == gateway.node.device_id
        and isinstance(f.payload, DeviceHeartbeat | CapacityReport)
    ]

    first, second = frames[0].payload, frames[1].payload
    assert isinstance(first, DeviceHeartbeat)
    assert first.level is RuntimeLevel.GATEWAY and first.lease_ids == ()
    assert first.hive_started_processes == 0 and first.outbox_pending == 0
    assert isinstance(second, CapacityReport)
    assert second.trigger is CapacityTrigger.RECONNECTED
    assert second.cell_id == gateway.node.cell_id


async def test_a_capacity_report_is_a_full_snapshot_of_a_gateway_that_hosts_no_models(
    gateway: Gateway,
) -> None:
    report = gateway.driver.frames_of(CapacityReport)[0].payload

    assert isinstance(report, CapacityReport)
    assert report.model_servers == ()
    assert report.host.cores > 0 and report.host.memory_bytes > 0
    usage = report.usage
    assert (usage.sub_bees_active, usage.model_vram_bytes, usage.seats_exported) == (0, 0, 0)


async def test_heartbeats_keep_coming_at_the_enrolled_interval_while_a_command_runs(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    before = len(driver.frames_of(DeviceHeartbeat))

    async with driver.leased() as lease:
        run = await driver.run_exec(lease, python_argv("import time; time.sleep(1.6)"))

    assert run.exit.exit_code == 0
    # 1.6 s at a 0.5 s beat is three beats; allow for the first to land a moment late.
    assert len(driver.frames_of(DeviceHeartbeat)) - before >= 2


async def test_a_gateway_listens_nowhere_and_dials_only_the_queen(gateway: Gateway) -> None:
    connections = psutil.Process(gateway.process.pid).net_connections(kind="inet")

    assert not [c for c in connections if c.status == psutil.CONN_LISTEN]
    queen_port = int(gateway.hive.url.rsplit(":", 1)[1])
    assert {c.raddr.port for c in connections if c.raddr} <= {queen_port}


async def test_a_gateway_speaks_major_one_and_names_its_minor(gateway: Gateway) -> None:
    pong = await gateway.driver.ask_ping()

    major, minor = pong.version.split(".")
    assert major == "1" and 0 <= int(minor) <= PROTOCOL_MINOR


# ── Housekeeping (section 9.5) ─────────────────────────────────────────────────────


async def test_a_ping_from_the_queen_or_the_warden_is_answered_with_a_pong(
    gateway: Gateway,
) -> None:
    driver = gateway.driver

    queen = await driver.ask_ping(as_queen=True)
    warden = await driver.ask_ping(as_queen=False)

    assert isinstance(queen.payload, Pong) and isinstance(warden.payload, Pong)
    assert queen.correlation_id != warden.correlation_id


async def test_a_request_kind_the_gateway_does_not_implement_is_answered_unsupported(
    gateway: Gateway,
) -> None:
    driver, node = gateway.driver, gateway.node
    request = Colonize(
        cell_id=node.cell_id,
        device_id=node.device_id,
        action=ColonizeAction.PREPARE,
        warden_id=node.warden_id,
        runtime_version="0.1.0",
        warden_url=None,
        deadline_s=60.0,
        reason="The conformance suite asks for a rung the gateway may not have.",
    )

    try:
        answer = await driver.ask(request, as_queen=True)
    except DeviceRequestError as refused:
        # A gateway that does not colonize says so and keeps the link (section 17).
        assert refused.device_code == "pollen.unsupported"
    else:
        assert isinstance(answer.payload, Colonized)  # One that does answers in its own message.
    await driver.sync()


# ── Who may speak (section 6) ───────────────────────────────────────────────────────


async def test_a_frame_from_a_sender_that_is_neither_queen_nor_warden_is_dropped_unanswered(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    stranger = str(new_warden_id(SystemClock()))

    async with driver.link.exchange() as exchange:
        await exchange.send(Ping(), sender=stranger)
        await driver.sync()  # Handled in order: anything the stranger's ping drew has arrived.
        with pytest.raises(DeviceTimeoutError):
            await exchange.reply(0.0)


async def test_a_queen_only_kind_from_the_warden_changes_nothing(terms: Gateway) -> None:
    driver, node = terms.driver, terms.node
    read_only_terms = node.model_copy(
        update={
            "access_level": AccessLevel.READ_ONLY,
            "granted_capabilities": granted_capabilities(AccessLevel.READ_ONLY, node.cell_id),
        }
    )

    await driver.link.send(
        node_update_for(read_only_terms, "The Warden claims to be the Queen."),
        sender=driver.warden,
    )
    await driver.sync()

    async with driver.leased() as lease:  # Still FULL: the forged update was dropped.
        assert (await driver.run_exec(lease, python_argv("pass"))).exit.exit_code == 0


# ── Leases (section 9.1) ────────────────────────────────────────────────────────────


async def test_a_lease_opens_at_the_lower_access_level_with_scratch_and_a_dead_man_limit(
    gateway: Gateway,
) -> None:
    driver = gateway.driver

    lease = await driver.open_lease(WireAccess.SCRATCH)
    try:
        assert lease.access_level is WireAccess.SCRATCH
        assert lease.holder == gateway.node.warden_id and lease.cell_id == gateway.node.cell_id
        assert os.path.isabs(lease.scratch_root) and is_directory(lease.scratch_root)
        assert lease.dead_man_s == gateway.node.offline_limit_s
        assert lease.comb_shield.value == "MEADOW"
    finally:
        await driver.release(lease)


async def test_releasing_a_lease_deletes_its_scratch_and_reports_the_device_as_found(
    gateway: Gateway,
) -> None:
    driver = gateway.driver
    lease = await driver.open_lease()
    await driver.put_file(lease, "marker.txt", b"here")

    released = await driver.release(lease, cause=ReleaseCause.CANCELLED)

    assert released.lease_id == lease.lease_id and released.cause is ReleaseCause.CANCELLED
    assert released.is_restored and released.residual_paths == ()
    assert not exists(lease.scratch_root)


async def test_releasing_a_lease_kills_every_process_it_started(gateway: Gateway) -> None:
    driver = gateway.driver
    lease = await driver.open_lease()
    started = await driver.start_background(lease, python_argv(_SLEEP_FOREVER))
    async with driver.start_exec(
        lease, python_argv(f"print('up', flush=True); {_SLEEP_FOREVER}")
    ) as r:
        running = r
        await running.wait_for_output()
        assert alive(started.pid)

        released = await driver.release(lease, urgency=Urgency.IMMEDIATE)
        run = await running.finish()

    assert released.killed_processes >= 2
    assert not alive(started.pid)
    assert run.exit.outcome.value == "KILLED"
    assert not exists(lease.scratch_root)


@pytest.mark.parametrize("who", ["another_cell", "another_holder"])
async def test_a_lease_for_another_cell_or_holder_isrefused(gateway: Gateway, who: str) -> None:
    driver, clock = gateway.driver, SystemClock()
    if who == "another_cell":
        asked = driver.open_lease(cell_id=new_cell_id(clock))
    else:
        asked = driver.open_lease(holder=new_warden_id(clock))

    assert await refused(asked) == "pollen.lease_refused"


async def test_a_teardown_for_a_lease_not_held_draws_lease_not_open(gateway: Gateway) -> None:
    driver = gateway.driver
    lease = await driver.open_lease()
    await driver.release(lease)

    code = await driver.refusal(driver.teardown(lease.lease_id), as_queen=True)

    assert code == "hive.session.lease_not_open"


async def test_a_teardown_that_names_no_lease_is_refused_because_a_real_cell_is_never_retired(
    gateway: Gateway,
) -> None:
    driver = gateway.driver

    code = await driver.refusal(driver.teardown(None), as_queen=True)

    assert code == "pollen.lease_refused"


# ── Capabilities (section 10) ───────────────────────────────────────────────────────


async def test_a_read_only_gateway_runs_no_command_and_writes_nothing_but_reads(
    read_only: Gateway, tmp_path: Path
) -> None:
    driver, readable = read_only.driver, tmp_path / "readable.txt"
    write_file(readable, b"read me")

    async with driver.leased() as lease:  # Asks for FULL; the invite caps it.
        assert lease.access_level is WireAccess.READ_ONLY
        assert await refused(driver.run_exec(lease, python_argv("pass"))) == DENIED
        assert await refused(driver.put_file(lease, "note.txt", b"x")) == DENIED
        assert await refused(driver.start_background(lease, python_argv("pass"))) == DENIED
        data, _chunks, closing = await driver.get_file(lease, str(readable))
        assert data == b"read me" and closing.size_bytes == 7


async def test_a_scratch_gateway_runs_commands_and_writes_only_inside_scratch(
    scratch: Gateway, tmp_path: Path
) -> None:
    driver, outside = scratch.driver, tmp_path / "outside.txt"

    async with driver.leased() as lease:
        assert lease.access_level is WireAccess.SCRATCH and lease.allowed_paths == ()
        assert (await driver.put_file(lease, "inside.txt", b"ok")).size_bytes == 2
        assert (await driver.run_exec(lease, python_argv("print(1)"))).exit.exit_code == 0
        assert await refused(driver.put_file(lease, str(outside), b"no")) == DENIED
        # A relative path that climbs out of scratch is judged where it lands, not as written.
        assert await refused(driver.put_file(lease, "../escape.txt", b"no")) == DENIED
        assert not exists(str(outside))


async def test_a_change_outside_scratch_is_recorded_first_and_undone_on_release(
    gateway: Gateway, tmp_path: Path
) -> None:
    driver = gateway.driver
    existing, created = tmp_path / "existing.txt", tmp_path / "made" / "deep" / "created.txt"
    write_file(existing, b"original")

    async with driver.leased() as lease:
        assert lease.allowed_paths  # FULL holds cell:outside_scratch, and says where.
        await driver.put_file(lease, str(existing), b"changed")
        await driver.put_file(lease, str(created), b"new")
        assert read_file(existing) == b"changed" and exists(str(created))
        released = await driver.release(lease)

    assert released.is_restored and released.residual_paths == ()
    assert read_file(existing) == b"original"
    assert not exists(str(created)) and not exists(str(created.parent.parent))


# ── Standing terms (section 9.5, enrolment.md section 10) ──────────────────────────────


async def test_a_node_update_applies_from_the_next_request_even_inside_an_open_lease(
    terms: Gateway,
) -> None:
    driver = terms.driver
    async with driver.leased() as lease:
        assert (await driver.run_exec(lease, python_argv("pass"))).exit.exit_code == 0
        try:
            await driver.update_terms(access=AccessLevel.READ_ONLY)

            assert await refused(driver.run_exec(lease, python_argv("pass"))) == DENIED
        finally:
            await driver.update_terms(access=AccessLevel.FULL)
        assert (await driver.run_exec(lease, python_argv("pass"))).exit.exit_code == 0


async def test_a_node_update_sets_the_dead_man_limit_the_next_lease_reports(terms: Gateway) -> None:
    driver = terms.driver
    try:
        await driver.update_terms(offline_limit_s=7.5)

        async with driver.leased() as lease:
            assert lease.dead_man_s == 7.5
    finally:
        await driver.update_terms(offline_limit_s=terms.node.offline_limit_s)
