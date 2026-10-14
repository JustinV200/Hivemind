"""Integration: the Propolis checks, run for real against a container of the Cell image.

Roadmap phase 11: "On a Virtual Cell the same checks run against the image, with Tor disabled for
that tier" (docs/adr/0057). The unit tests give the judges text written by hand; this gives them
what a real kernel prints in a real container of `hivemind/base-ubuntu`. The probe is the shipped
`SessionPropolisProbe`, run over a session whose commands run in the container (`docker exec`,
`integration.docker_tunnel`), so the fixed commands themselves are tried too: that the image has
them, that they print what the parsers expect, that a missing IPv6 table is read as no IPv6 route.

The same container is first looked at as it starts, with a resolver of the VPN's network but no
tunnel (red on the route, the resolver and direct egress), and then again once a `tun0` with the
whole internet routed into it has been put in its network namespace (green on all five checks). A
resolver on the local network is not covered by the tunnel, a program named `tor` and a listener on
the SOCKS port each turn an otherwise tunnelled Cell red, the one `cell.attested` event of a real
Cell names the red checks and never what the Cell printed, and the Cell's own self-attestation
(`hivemind.cli.in_cell.propolis`, which the image ships) reaches the same verdicts as the one made
from outside. Last, the image's own Warden is booted in the container as a PROPOLIS Cell, with the
tunnel made first or not, and dials a Queen listening on the test's host: it announces PROPOLIS and
the four tunnel checks it makes of its own routes and resolvers, passed with the tunnel and failed
without it, and its heartbeat says whether the shield is verified.

`@pytest.mark.integration` (codingrules 14.2): skips cleanly when no daemon answers, when the Cell
image is not built (`HIVEMIND_TEST_CELL_IMAGE`, default `integration.docker_helpers.IMAGE`; build
it from this tree), or when this daemon cannot build the small `ip` helper image or give a
container `/dev/net/tun`. It assumes the daemon gives containers no IPv6 default route, which is
Docker's default.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises hive/propolis,
    cell/tunnel and cli/in_cell/propolis against the image of images/base-ubuntu.

Key invariants:
    - Every container is removed, whatever the test did: the janitor closes in a `finally`.
    - The Cell image is as shipped; the tunnel is made from outside it (docker_tunnel).

See Also:
    - tests/unit/hive/propolis/ for the same checks over hand-written output.
    - tests/e2e/test_virtual_cells_propolis.py for the provider and the Queen over a fake backend.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterator, Mapping

import pytest
from e2e.swarm.container import BRIDGE, CommandError, Container, daemon_answers, docker
from integration.docker_helpers import ALL_INTERFACES, IMAGE, WAIT_S
from integration.docker_tunnel import (
    HOST_ALIAS,
    TUNNEL_RESOLVER,
    CellJanitor,
    boot_warden,
    give_tunnel,
    poll,
    run_in_background,
    session_on,
    tools_image,
    tun_device_usable,
    warden_log,
)

from hivemind.cell import OsFamily
from hivemind.cell.tunnel import CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS, CHECK_IPV6
from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis import (
    CHECK_TOR,
    PROPOLIS_CHECK_NAMES,
    SessionPropolisProbe,
    attest_propolis_cell,
)
from hivemind.pheromone import TrailQuery, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock, SystemClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.messages.labels import Urgency
from waggle.signing import Ed25519Signer
from waggle.transport.websocket_server import WebSocketServer

pytestmark = pytest.mark.integration
needs_a_tunnel = pytest.mark.usefixtures("tunnel_ready")  # Skips unless a tun can be made here.

_IMAGE = os.environ.get("HIVEMIND_TEST_CELL_IMAGE", IMAGE)
PASS, FAIL = CheckStatus.PASS, CheckStatus.FAIL
_TUNNELLED = dict.fromkeys(PROPOLIS_CHECK_NAMES, PASS)
# A Cell on its own network: its default route, its resolver and its direct egress are all outside
# the tunnel it does not have; it has no IPv6 route and no Tor.
_UNTUNNELLED = {**_TUNNELLED, CHECK_DEFAULT_ROUTE: FAIL, CHECK_DNS: FAIL, CHECK_EGRESS: FAIL}
_SOCKS_PORT = 9050  # Where a Tor client listens by default.
# Run in the Cell as a stand-in for Tor's SOCKS listener: bind the port, then stay up.
_LISTEN = (
    "import socket, time\n"
    "s = socket.socket()\n"
    f"s.bind(('127.0.0.1', {_SOCKS_PORT}))\n"
    "s.listen()\n"
    "time.sleep(300)\n"
)
_CAN_CONNECT = f"import socket; socket.create_connection(('127.0.0.1', {_SOCKS_PORT}), 2).close()"
# Run in the Cell: its own attestation, the four tunnel checks as name -> passed.
_OWN_CHECKS = (
    "import json\n"
    "from hivemind.cli.in_cell.propolis import own_tunnel_attestation\n"
    "print(json.dumps({c.name: c.has_passed for c in own_tunnel_attestation()}))\n"
)


@pytest.fixture(scope="module")
def cell_image() -> str:
    """The Cell image to start containers from; the module skips without a daemon that has it."""
    if not daemon_answers():
        pytest.skip("no Docker daemon reachable")
    if docker("image", "inspect", _IMAGE, check=False).exit_code != 0:
        pytest.skip(f"{_IMAGE} is not built locally; build it from this tree")
    return _IMAGE


@pytest.fixture(scope="module")
def tunnel_ready(cell_image: str) -> None:
    """Skip a test that needs a tunnel unless the daemon can build the helper and give it a tun."""
    try:
        tools_image()
    except CommandError as failure:
        pytest.skip(f"the ip helper image cannot be built here: {failure}")
    if not tun_device_usable():
        pytest.skip("this daemon cannot give a container /dev/net/tun")


@pytest.fixture
def janitor(cell_image: str) -> Iterator[CellJanitor]:
    """A janitor of Cells of the image, removing every container a test started."""
    janitor = CellJanitor(cell_image)
    try:
        yield janitor
    finally:
        janitor.close()


async def _checks(cell: Container) -> dict[str, CheckResult]:
    """The shipped Propolis probe's report on `cell`, its commands run in the container."""
    return await SessionPropolisProbe(session_on(cell), OsFamily.LINUX).checks()


def _statuses(checks: Mapping[str, CheckResult]) -> dict[str, CheckStatus]:
    """Each check's verdict by name, without the reasons."""
    return {name: result.status for name, result in checks.items()}


def _start_a_tor_process(cell: Container) -> None:
    """Run a program whose name is `tor` (a link to `sleep`) and wait until it shows."""
    # Made in the image's working directory, which the Cell's user can write to.
    docker("exec", cell.name, "ln", "-s", "/bin/sleep", "tor")
    run_in_background(cell, ["./tor", "300"])
    poll(lambda: "tor" in cell.processes().values(), "the program named tor to start")


def _start_a_socks_listener(cell: Container) -> None:
    """Listen on Tor's SOCKS port in the Cell and wait until a connection to it succeeds."""
    run_in_background(cell, ["python3", "-c", _LISTEN])
    poll(
        lambda: cell.exec(["python3", "-c", _CAN_CONNECT], check=False).exit_code == 0,
        f"a listener on port {_SOCKS_PORT}",
    )


async def _boot_as_a_propolis_cell(cell: Container) -> tuple[CellReady, CellHeartbeat]:
    """Boot the image's Warden in `cell` as a PROPOLIS Cell; return what it announced to a Queen.

    The Queen is a bare listener on this machine that the container dials through the host alias:
    the Cell's CellReady, its CapacityReport and its first CellHeartbeat are read off it, and then
    it is told to shut down, so the Warden stops as it would in a Hive.
    """
    clock, queen = SystemClock(), Ed25519Signer.generate()
    hive_id, queen_node_id = new_hive_id(clock), new_node_id(clock)
    server = WebSocketServer(Codec(signer=queen), host=ALL_INTERFACES)
    await server.start()
    bootstrap = {
        "HIVEMIND_QUEEN_WAGGLE_URL": f"ws://{HOST_ALIAS}:{server.port}",
        "HIVEMIND_COMB_SHIELD": "PROPOLIS",
        "HIVEMIND_CELL_ID": new_cell_id(clock),
        "HIVEMIND_HIVE_ID": hive_id,
        "HIVEMIND_QUEEN_NODE_ID": queen_node_id,
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": queen.public_key_bytes.hex(),
    }
    try:
        boot_warden(cell, bootstrap)
        accepted = await asyncio.wait_for(anext(server.connections()), timeout=WAIT_S)
        frames = accepted.receive()
        ready = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
        await asyncio.wait_for(anext(frames), timeout=WAIT_S)  # The CapacityReport.
        beat = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
        assert isinstance(ready, CellReady)
        assert isinstance(beat, CellHeartbeat)
        hop = Hop(sender=hive_id, recipient=ready.warden_id, node_id=queen_node_id)
        stop = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
        await accepted.send(wrap(stop, hop, clock=clock))
    except TimeoutError:
        pytest.fail(f"the Warden never announced; its log:\n{warden_log(cell)}")
    finally:
        await server.close()
    return ready, beat


def _own_checks(cell: Container) -> dict[str, bool]:
    """Run the image's own Propolis self-attestation in `cell`; skip if the image lacks it."""
    done = cell.exec(["python3", "-c", _OWN_CHECKS], check=False)
    if "ModuleNotFoundError" in done.stderr:
        pytest.skip(
            f"{_IMAGE} predates the in-Cell Propolis attestation; rebuild it from this tree"
        )
    checks: dict[str, bool] = json.loads(done.require().stdout)
    return checks


@needs_a_tunnel
async def test_the_same_cell_is_red_without_a_tunnel_and_green_with_one(
    janitor: CellJanitor,
) -> None:
    cell = janitor.start_cell(TUNNEL_RESOLVER)

    before = await _checks(cell)
    give_tunnel(cell)
    after = await _checks(cell)

    assert _statuses(before) == _UNTUNNELLED
    assert _statuses(after) == _TUNNELLED


@needs_a_tunnel
async def test_a_tunnel_does_not_make_a_resolver_on_the_local_network_safe(
    janitor: CellJanitor,
) -> None:
    # The resolver is the docker bridge's own address: on the Cell's connected network, so its
    # queries leave by the interface the tunnel does not cover, whatever routes the rest.
    cell = janitor.start_cell(BRIDGE.gateway)
    give_tunnel(cell)

    checks = await _checks(cell)

    assert _statuses(checks) == {**_TUNNELLED, CHECK_DNS: FAIL}


@needs_a_tunnel
@pytest.mark.parametrize(
    "start",
    [_start_a_tor_process, _start_a_socks_listener],
    ids=["a program named tor", "a listener on the SOCKS port"],
)
async def test_tor_in_a_tunnelled_cell_turns_it_red_on_the_tor_check_alone(
    janitor: CellJanitor, start: Callable[[Container], None]
) -> None:
    cell = janitor.start_cell(TUNNEL_RESOLVER)
    give_tunnel(cell)
    assert _statuses(await _checks(cell)) == _TUNNELLED  # Green until Tor is there.

    start(cell)
    checks = await _checks(cell)

    assert _statuses(checks) == {**_TUNNELLED, CHECK_TOR: FAIL}


@needs_a_tunnel
async def test_a_real_cells_attestation_is_recorded_red_then_green_without_what_it_printed(
    janitor: CellJanitor,
) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=new_hive_id(clock), node_id=new_node_id(clock)
    )
    cell = janitor.start_cell(TUNNEL_RESOLVER)
    probe = SessionPropolisProbe(session_on(cell), OsFamily.LINUX)
    cell_id = new_cell_id(clock)

    red = await attest_propolis_cell(probe, recorder, cell_id)
    give_tunnel(cell)
    green = await attest_propolis_cell(probe, recorder, cell_id)

    assert red.red == (CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS)
    assert green.passed
    events = await trail.query(TrailQuery(kind="cell.attested"))
    assert [event.payload["passed"] for event in events] == [False, True]
    # Names and short reasons only: not an interface, an address or a line of the Cell's output.
    recorded = json.dumps([event.payload for event in events])
    for printed in ("eth0", "tun0", "nameserver", TUNNEL_RESOLVER, "172.", "/proc"):
        assert printed not in recorded


@needs_a_tunnel
async def test_the_cells_own_attestation_agrees_with_the_one_made_from_outside(
    janitor: CellJanitor,
) -> None:
    cell = janitor.start_cell(TUNNEL_RESOLVER)

    own_before, outside_before = _own_checks(cell), await _checks(cell)
    give_tunnel(cell)
    own_after, outside_after = _own_checks(cell), await _checks(cell)

    for own, outside in ((own_before, outside_before), (own_after, outside_after)):
        assert own == {name: outside[name].status is PASS for name in own}
    assert set(own_before) == {CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_IPV6, CHECK_EGRESS}
    assert not own_before[CHECK_DEFAULT_ROUTE]
    assert all(own_after.values())


@needs_a_tunnel
@pytest.mark.parametrize("tunnelled", [False, True], ids=["no tunnel", "a tunnel"])
async def test_the_images_warden_boots_as_a_propolis_cell_and_announces_what_it_sees(
    janitor: CellJanitor, tunnelled: bool
) -> None:
    cell = janitor.start_cell(TUNNEL_RESOLVER, reach_host=True)
    if tunnelled:
        give_tunnel(cell)

    ready, beat = await _boot_as_a_propolis_cell(cell)

    # A report and not a gate: a Cell with no tunnel still announces, its checks failed.
    assert ready.comb_shield is WireCombShieldLevel.PROPOLIS
    assert {check.name: check.has_passed for check in ready.attestation} == {
        CHECK_DEFAULT_ROUTE: tunnelled,
        CHECK_DNS: tunnelled,
        CHECK_IPV6: True,
        CHECK_EGRESS: tunnelled,
    }
    assert beat.is_shield_verified is tunnelled
