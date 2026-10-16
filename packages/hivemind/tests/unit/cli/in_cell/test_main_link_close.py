"""Tests for hivemind.cli.in_cell.main: the Queen link is closed when a start fails after the dial.

`run_in_cell_warden` connects, announces and builds the Warden's deps before the `try/finally`
that closes the link around the Warden's run; a failure in that window (a frame the Queen's
listener dropped the socket under, a provider table the deps cannot be built from) used to leave
the connected transport open, against the module's invariant that a built link is closed on
every way out. Each test fails one step from outside and watches the Queen's end of the socket
end, as it does when the Cell hangs up on its own account.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules section 3: tests/unit mirrors src/
    one-to-one), the part test_main.py's cancellation test does not reach: a failure before the
    Warden exists.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.main for the module under test.
    - test_main.py for the sequence that succeeds, and test_main_night_veil_redial.py for the
      mending link's own suite.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.tor_stand import ONION, ONION_PORT, TorStand, tor_stand

from hivemind.cli.in_cell import link as link_module
from hivemind.cli.in_cell.main import run_in_cell_warden
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.ids import new_hive_id, new_node_id
from waggle.signing import Ed25519Signer
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
QUIET_S = 0.3  # Long enough that a redial made on purpose would have reached the proxy by now.
_CLOCK = SystemClock()
_CELL_ID = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"
# The module, not the `main` function the package's face exports under the same name.
main_module = importlib.import_module("hivemind.cli.in_cell.main")


class _StepError(Exception):
    """What a test makes one start-up step raise."""


def _fail(*_args: object, **_kwargs: object) -> None:
    """Stand in for a step that raises after the link is connected."""
    raise _StepError("a step after the dial failed")


async def _until_ended(frames: AsyncIterator[Envelope]) -> list[Envelope]:
    """Read a connection's frames until its peer closes it; return what it sent first."""
    return [frame async for frame in frames]


def _environ(server_uri: str, queen_signer: Ed25519Signer, scratch_root: Path) -> dict[str, str]:
    """A real `HIVEMIND_*` environment naming `server_uri` as this MEADOW Cell's own Queen."""
    return {
        "HIVEMIND_QUEEN_WAGGLE_URL": server_uri,
        "HIVEMIND_CELL_ID": _CELL_ID,
        "HIVEMIND_HIVE_ID": new_hive_id(_CLOCK),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(_CLOCK),
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": queen_signer.public_key_bytes.hex(),
        "HIVEMIND_SCRATCH_ROOT": str(scratch_root),
    }


@pytest.fixture
async def queen() -> AsyncIterator[tuple[WebSocketServer, Ed25519Signer]]:
    """A signed loopback listener standing in for the Queen's."""
    signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=signer))
    await server.start()
    try:
        yield server, signer
    finally:
        await server.close()


# A step that fails after the dial, as (module, name, frames sent before it): the announcement's
# second frame, and the build of the Warden's deps after all three frames.
_FAILURES = [
    (link_module, "send_capacity_report", 1),
    (main_module, "build_in_cell_warden_deps", 3),
]


@pytest.mark.parametrize("failure", _FAILURES, ids=[step for _module, step, _n in _FAILURES])
async def test_a_failure_after_the_dial_closes_the_link_and_raises(
    queen: tuple[WebSocketServer, Ed25519Signer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: tuple[object, str, int],
) -> None:
    server, signer = queen
    module, step, frames_before = failure
    monkeypatch.setattr(module, step, _fail)
    run_task = asyncio.ensure_future(
        run_in_cell_warden(_environ(server.uri, signer, tmp_path), _CLOCK)
    )
    accepted = await asyncio.wait_for(anext(server.connections()), timeout=WAIT_S)

    with pytest.raises(_StepError):
        await asyncio.wait_for(run_task, timeout=WAIT_S)

    # The Cell hung up on its own account: the Queen's stream of its frames ends, after exactly
    # the frames that went out before the step failed.
    sent = await asyncio.wait_for(_until_ended(accepted.receive()), timeout=WAIT_S)
    assert len(sent) == frames_before


@pytest.fixture
async def stand() -> AsyncIterator[TorStand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    async with tor_stand() as made:
        yield made


async def test_a_night_veil_cell_whose_build_fails_closes_its_mending_link(
    stand: TorStand, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "build_in_cell_warden_deps", _fail)
    run_task = asyncio.ensure_future(run_in_cell_warden(stand.environ(tmp_path), _CLOCK))
    connections = stand.server.connections()
    accepted = await asyncio.wait_for(anext(connections), timeout=WAIT_S)

    with pytest.raises(_StepError):
        await asyncio.wait_for(run_task, timeout=WAIT_S)

    # The link that mends itself was closed, not left redialing Tor for a Cell that no longer
    # exists: the Queen's end of the one connection ends, and no second dial reaches the proxy.
    await asyncio.wait_for(_until_ended(accepted.receive()), timeout=WAIT_S)
    arrival = asyncio.ensure_future(anext(connections))
    done, _pending = await asyncio.wait({arrival}, timeout=QUIET_S)
    arrival.cancel()
    assert not done
    assert stand.proxy.requests == [(ONION, ONION_PORT)]
