"""Run the waggle_echo demo's client role: dial, exchange, tamper, survive an outage, replay.

Waggle is the Hive's bee-to-bee wire protocol. This is the dialling half of the phase 1 exit
demo (scripts/waggle_echo.py): a ``WebSocketClientTransport`` with a codec that signs every
frame and verifies the server's. It plays the scenario in order and reports each milestone as
one ``EVENT <name> [detail]`` line on stdout, which is all the orchestrator reads: a ``Ping``
answered by a correlated ``Pong``; a signed ``TaskAssign`` answered by a correlated
``TaskProgress``; a frame this codec signed and then altered by one byte, sent through a bare
websockets connection and refused with close code 1008; the link dropping when the orchestrator
kills the server (``ConnectionLostError`` out of ``receive``); three Pings queued in a durable
``Outbox`` while the server is down; and, once the server is back on the same port, a reconnect
through the transport's capped backoff and ``replay_outbox`` draining the queue, each replayed
Ping answered by its correlated Pong.

Fits into the Hive:
    Layer: none (a demo, not shipped code). Started by scripts/waggle_echo.py as a child
    process; loads its keys through scripts/waggle_echo_keys.py; calls into waggle.transport,
    waggle.outbox, waggle.outbox.replay, waggle.envelope, waggle.messages and the websockets
    library (for the one deliberately dishonest connection).

Key invariants:
    - stdout carries only EVENT lines, one per milestone, flushed as they happen.
    - Every await on the network is bounded by a named timeout, so a server that misbehaves
      makes this process exit non-zero rather than hang.
    - The tampered frame differs from a signed one by exactly one byte inside the payload
      text, never in the signature, so what the server refuses is tampering and nothing else.

See Also:
    - scripts/waggle_echo.py for the scenario this role plays its part in.
    - scripts/waggle_echo_server.py for the peer.
    - waggle.transport.websocket_client and waggle.outbox.replay for what is exercised here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from waggle_echo_keys import CLIENT_ROLE, load_keyring
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from waggle import (
    Clock,
    Codec,
    ConnectionLostError,
    Envelope,
    Hop,
    MessageId,
    Outbox,
    SystemClock,
    new_cell_id,
    new_grant_id,
    new_task_id,
    replay_outbox,
    wrap,
)
from waggle.messages import (
    AccuracyBar,
    HoneyClearance,
    Ping,
    Postcondition,
    PostconditionKind,
    TaskAssign,
    TaskProgress,
    Tempo,
)
from waggle.messages.task.assignment import WorkerRole
from waggle.messages.task.reports import TaskStage
from waggle.transport import DEFAULT_HOST, WebSocketClientTransport

QUEUED_PINGS = 3  # Enough to show ordering on replay; the exit criterion says "an outbox".
CONNECT_TIMEOUT_S = 15.0  # The first dial: the server printed READY, so this is loopback latency.
REPLY_TIMEOUT_S = 10.0  # A correlated reply on loopback arrives in milliseconds; this is a bound.
OUTAGE_TIMEOUT_S = 30.0  # How long to wait for the orchestrator to kill the server.
DIAL_TIMEOUT_S = 0.5  # Per attempt: a refused loopback dial takes ~2 s on Windows without it.
RECONNECT_ATTEMPTS = 8  # Waits of 0.5, 1, 2, 4, 8, 16, 30 s between dials: 61.5 s in all.
RECONNECT_TIMEOUT_S = 90.0  # Bounds the whole backoff sequence above with room to spare.
OBJECTIVE = "Echo this assignment back as a started task."  # Starts with a letter: see _tamper.
CASE_BIT = 0x20  # XOR on an ASCII letter flips its case: still valid JSON, not what was signed.

__all__ = ["QUEUED_PINGS", "run_client"]


class ScenarioError(Exception):
    """The server broke the scenario: an answer that never came, or the wrong one."""


def run_client(port: int, keys_dir: Path, outbox_path: Path) -> int:
    """Play the client's side of the scenario; the synchronous entry the CLI dispatches to.

    Args:
        port: The loopback port the server listens on, before and after its restart.
        keys_dir: The directory scripts/waggle_echo_keys.write_keys produced.
        outbox_path: Where the outbox log is kept while the server is down; its directory exists.

    Returns:
        0 once every replayed Ping has been answered.

    Raises:
        ScenarioError: The server broke the scenario.
        TimeoutError: A bounded wait expired.
        waggle.WaggleError: The transport or codec refused something on this side.
    """
    return asyncio.run(_play(port, keys_dir, outbox_path))


async def _play(port: int, keys_dir: Path, outbox_path: Path) -> int:
    """Run the scenario's five acts in order on one client transport."""
    keyring = load_keyring(keys_dir, CLIENT_ROLE)
    clock = SystemClock()
    uri = f"ws://{DEFAULT_HOST}:{port}"
    hop = Hop(
        sender=keyring.own.address, recipient=keyring.peer.address, node_id=keyring.own.node_id
    )
    client = WebSocketClientTransport(
        uri, keyring.codec, clock, max_attempts=RECONNECT_ATTEMPTS, open_timeout_s=DIAL_TIMEOUT_S
    )
    # Act 1: a plain request and a signed one, each answered by a correlated reply.
    async with asyncio.timeout(CONNECT_TIMEOUT_S):
        await client.connect()
    assign = await _exchange(client, hop, clock)
    # Act 2: the same signed frame, altered by one byte, on a connection the codec never sees.
    # The honest connection is closed first because the server serves one at a time.
    await client.close()
    await _tamper(uri, keyring.codec.encode(assign))
    # Act 3: reconnect and wait for the orchestrator to kill the server under us.
    async with asyncio.timeout(CONNECT_TIMEOUT_S):
        await client.connect()
    _event("waiting_for_outage")
    await _await_outage(client)
    # Act 4: what cannot be sent waits on disk, in order.
    outbox, queued = await _queue_pings(outbox_path, keyring.codec, hop, clock)
    # Act 5: the server is back on the same port; dial with backoff, drain the queue, collect.
    async with asyncio.timeout(RECONNECT_TIMEOUT_S):
        await client.connect()
    _event("reconnected")
    report = await replay_outbox(outbox, client)
    _event("replayed", report.sent)
    answered = await _collect_replies(client, set(queued))
    _event("replay_answered", len(answered))
    await client.close()
    return 0


async def _exchange(client: WebSocketClientTransport, hop: Hop, clock: Clock) -> Envelope:
    """Send a Ping and a TaskAssign, wait for each one's reply, and return the assign envelope."""
    ping = wrap(Ping(), hop, clock=clock)
    await client.send(ping)
    await _collect_replies(client, {ping.id})
    _event("pong")
    assign = wrap(_assignment(clock), hop, clock=clock)
    await client.send(assign)
    (progress,) = await _collect_replies(client, {assign.id})
    # The reply must be the Warden's evidence that the task started, not merely something
    # correlated to the assign; anything else would mean the server misread the request.
    started = (
        isinstance(progress.payload, TaskProgress) and progress.payload.stage is TaskStage.STARTED
    )
    if not started:
        raise ScenarioError(
            f"Expected a STARTED task.progress for {assign.id}, got {progress.kind}."
        )
    _event("task_accepted")
    return assign


def _assignment(clock: Clock) -> TaskAssign:
    """A minimal valid TaskAssign: a root goal for a DRONE on the WORKER slot with one check."""
    task_id = new_task_id(clock)
    return TaskAssign(
        task_id=task_id,
        goal_id=task_id,  # Equal to task_id for a root goal (the model's own rule).
        cell_id=new_cell_id(clock),
        role=WorkerRole.DRONE,
        slot="WORKER",
        objective=OBJECTIVE,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.COMMAND_EXITS_ZERO,
                subject="true",
                argv=("true",),
                expected=None,
            ),
        ),
        tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        clearance=HoneyClearance.C0,
        grant_id=new_grant_id(clock),
        attempt=1,
        resume_from=None,
        reason="waggle_echo demo: a signed request across a process boundary.",
    )


async def _tamper(uri: str, frame: bytes) -> None:
    """Send ``frame`` with one payload byte flipped over a bare connection; report the close."""
    # Flip the case of the objective's first letter: the JSON stays well formed, the kind and
    # version untouched, the payload still valid, and the signature no longer matches.
    marker = b'"objective":"'
    at = frame.index(marker) + len(marker)
    tampered = frame[:at] + bytes((frame[at] ^ CASE_BIT,)) + frame[at + 1 :]
    # A bare websockets client, not WebSocketClientTransport: the transport's codec would sign
    # the envelope afresh, and the point is to deliver bytes no honest codec would produce.
    # proxy=None for the same reason the transport gives: a loopback link is never rerouted.
    async with asyncio.timeout(REPLY_TIMEOUT_S):
        raw = await connect(uri, proxy=None)
        try:
            await raw.send(tampered)
            # The server's only correct answer is to close with 1008; a frame coming back
            # instead would mean it accepted what it should have refused.
            try:
                await raw.recv()
            except ConnectionClosed as exc:
                _event("tamper_rejected", exc.rcvd.code if exc.rcvd is not None else "none")
                return
            _event("tamper_accepted")
        finally:
            await raw.close()


async def _await_outage(client: WebSocketClientTransport) -> None:
    """Block on receive until the server is killed; a clean end of stream is a scenario error."""
    async with asyncio.timeout(OUTAGE_TIMEOUT_S):
        try:
            # The kill drops the TCP link without a close frame; anything that arrives before
            # it is not part of the scenario and is ignored.
            async for _ in client.receive():
                pass
        except ConnectionLostError:
            _event("link_lost")
            return
    raise ScenarioError("The server closed cleanly instead of being killed; no outage happened.")


async def _queue_pings(
    outbox_path: Path, codec: Codec, hop: Hop, clock: Clock
) -> tuple[Outbox, tuple[MessageId, ...]]:
    """Open the outbox at ``outbox_path`` and queue QUEUED_PINGS Pings in it; report the count."""
    # Opening reads and compacts the log: instant for a fresh file, but it is file I/O and the
    # event loop is not the place for it (codingrules 11); the same for each fsynced append.
    # The codec is passed for its frame limit only; the outbox stores unsigned frames.
    outbox = await asyncio.to_thread(Outbox, outbox_path, codec)
    queued: list[MessageId] = []
    for _ in range(QUEUED_PINGS):
        ping = wrap(Ping(), hop, clock=clock)
        await asyncio.to_thread(outbox.append, ping)
        queued.append(ping.id)
    _event("queued", len(outbox))
    return outbox, tuple(queued)


async def _collect_replies(
    transport: WebSocketClientTransport, wanted: set[MessageId]
) -> list[Envelope]:
    """Receive until a reply correlated to each id in ``wanted`` has arrived; return them in order.

    Typed on the concrete client transport rather than the Transport protocol because closing
    the receive generator early (``aclose``) is what keeps the connection usable afterwards,
    and the protocol promises only an AsyncIterator.
    """
    pending = set(wanted)
    replies: list[Envelope] = []
    async with asyncio.timeout(REPLY_TIMEOUT_S):
        receiver = transport.receive()
        try:
            # Anything not correlated to a wanted id is not part of the scenario and is skipped;
            # the generator is closed once the last reply is in so the connection stays usable.
            async for envelope in receiver:
                if envelope.correlation_id in pending:
                    pending.discard(envelope.correlation_id)
                    replies.append(envelope)
                if not pending:
                    break
        finally:
            await receiver.aclose()
    if pending:
        raise ScenarioError(f"The connection ended before replies to {sorted(pending)} arrived.")
    return replies


def _event(name: str, detail: object = None) -> None:
    """Print one milestone line, flushed so the orchestrator sees it the moment it happens."""
    print(f"EVENT {name}" if detail is None else f"EVENT {name} {detail}", flush=True)
