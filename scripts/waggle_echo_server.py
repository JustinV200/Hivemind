"""Run the waggle_echo demo's server role: answer Pings and TaskAssigns over a signed listener.

Waggle is the Hive's bee-to-bee wire protocol. This is the listener half of the phase 1 exit
demo (scripts/waggle_echo.py): a ``WebSocketServer`` on a loopback port with a codec that signs
every frame it sends and requires the client's signature on every frame it receives. It serves
one connection at a time, answering ``control.ping`` with a correlated ``Pong`` and
``task.assign`` with a correlated ``TaskProgress`` at stage STARTED, and it keeps accepting new
connections until the orchestrator asks it to stop by closing its stdin (a cross-platform stop
request: no signal handling is needed on Windows or Linux). It reports each milestone as one
``EVENT <name> [detail]`` line on stdout, and ``READY`` once bound, which is all the
orchestrator reads. A frame the codec refuses is reported as ``EVENT rejected <code>``; a
connection on which any envelope predates this process's start is reported as
``EVENT replayed <n>`` when it ends, because an envelope sent before the listener came up can
only have waited in the client's outbox while the listener was down.

Fits into the Hive:
    Layer: none (a demo, not shipped code). Started by scripts/waggle_echo.py as a child
    process; loads its keys through scripts/waggle_echo_keys.py; calls into waggle.transport,
    waggle.envelope, waggle.messages and waggle.clock.

Key invariants:
    - stdout carries only READY and EVENT lines, one per milestone, flushed as they happen.
    - A refused frame never ends the listener: the connection is closed with the mapped code
      (waggle.transport.base) and the next connection is served.

See Also:
    - scripts/waggle_echo.py for the scenario this role plays its part in.
    - scripts/waggle_echo_client.py for the peer.
    - waggle.transport.websocket_server for the listener this wraps.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from waggle_echo_keys import SERVER_ROLE, Identity, load_keyring

from waggle import Clock, Envelope, Hop, SystemClock, TransportError, WaggleError, wrap
from waggle.messages import HoneyClearance, Ping, Pong, TaskAssign, TaskProgress
from waggle.messages.task.reports import TaskStage
from waggle.transport import WebSocketServer, WebSocketTransport

READY_LINE = "READY"  # Printed once bound: the orchestrator's cue to start the client.
STARTED_SUMMARY = "The Warden accepted the task and spawned its Worker."  # TaskProgress.summary.

__all__ = ["READY_LINE", "run_server"]


def run_server(port: int, keys_dir: Path) -> int:
    """Serve on ``port`` until stdin closes; the synchronous entry the CLI dispatches to.

    Args:
        port: The loopback port to bind; the orchestrator picked it and restarts on the same one.
        keys_dir: The directory scripts/waggle_echo_keys.write_keys produced.

    Returns:
        0 once the listener has closed after the stop request.

    Raises:
        OSError: The port cannot be bound or a key file cannot be read.
    """
    return asyncio.run(_serve(port, keys_dir))


@dataclass(frozen=True, slots=True)
class _Responder:
    """What one connection's answers need: who we are, the clock, and when this process began."""

    own: Identity
    clock: Clock
    started_at: datetime  # Envelopes sent before this instant were queued while we were down.

    def answer(self, envelope: Envelope) -> Envelope | None:
        """Build the correlated reply for ``envelope``, or None for a kind the demo ignores."""
        # Every reply goes back to whoever sent the request, from our own address and node.
        hop = Hop(sender=self.own.address, recipient=envelope.sender, node_id=self.own.node_id)
        match envelope.payload:
            case Ping():
                reply: Pong | TaskProgress = Pong(received_at=self.clock.now())
            case TaskAssign() as assign:
                # STARTED is the Warden's evidence for ASSIGNED -> RUNNING; the demo's Warden
                # "starts" every task at once, which is all the exit criterion asks of it.
                reply = TaskProgress(
                    task_id=assign.task_id,
                    attempt=assign.attempt,
                    stage=TaskStage.STARTED,
                    summary=STARTED_SUMMARY,
                    clearance=HoneyClearance.C0,
                    fraction_done=0.0,
                    handoff=None,
                )
            case _:
                return None
        return wrap(reply, hop, clock=self.clock, correlation_id=envelope.id)


async def _serve(port: int, keys_dir: Path) -> int:
    """Bind, announce READY, serve connections one at a time, and stop when stdin closes."""
    keyring = load_keyring(keys_dir, SERVER_ROLE)
    clock = SystemClock()
    responder = _Responder(own=keyring.own, clock=clock, started_at=clock.now())
    server = WebSocketServer(keyring.codec, port=port)
    # Binds loopback; instant. OSError (port in use) propagates and the orchestrator reports it.
    await server.start()
    print(READY_LINE, flush=True)
    stop = asyncio.Event()
    _watch_stdin(asyncio.get_running_loop(), stop)
    # The accept loop and the stop watcher run under one TaskGroup (codingrules 11): closing the
    # server ends connections(), so the accept task returns and the group completes.
    async with asyncio.TaskGroup() as group:
        group.create_task(_accept_loop(server, responder))
        # Unbounded on purpose: the server lives until the orchestrator says otherwise.
        await stop.wait()
        await server.close()
    return 0


async def _accept_loop(server: WebSocketServer, responder: _Responder) -> None:
    """Serve each accepted connection to its end before taking the next (one at a time)."""
    # Sequential by design: the demo's client holds one connection at a time, and serving in
    # order keeps the event lines deterministic for the orchestrator to check.
    async for transport in server.connections():
        await _serve_one(transport, responder)


async def _serve_one(transport: WebSocketTransport, responder: _Responder) -> None:
    """Answer every envelope on ``transport`` until it ends, reporting refusals and replays."""
    replayed = 0
    try:
        # Network reads: each resolves when a frame arrives or the connection ends; a refused
        # frame raises after the transport has closed with the mapped code.
        async for envelope in transport.receive():
            # sent_at is preserved through the outbox (waggle.outbox_replay), so an envelope
            # older than this process was queued during the outage and is now being replayed.
            if envelope.sent_at < responder.started_at:
                replayed += 1
            reply = responder.answer(envelope)
            if reply is not None:
                await transport.send(reply)
    except TransportError as exc:
        # The peer vanished mid-connection: not a refusal, just the link; report and carry on.
        print(f"EVENT link_lost {exc.code}", flush=True)
    except WaggleError as exc:
        # A codec or signature refusal: the transport already closed with 1002/1008/1009 and the
        # stable error code as the close reason; the orchestrator matches this line against it.
        print(f"EVENT rejected {exc.code}", flush=True)
    finally:
        await transport.close()
    if replayed:
        print(f"EVENT replayed {replayed}", flush=True)


def _watch_stdin(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Set ``stop`` from a daemon thread once stdin yields a line or EOF (the stop request)."""

    def watch() -> None:
        """Block on stdin; a closed pipe reads as EOF, which is the orchestrator's stop."""
        sys.stdin.readline()
        # The loop may already be gone if the server stopped for another reason; a daemon
        # thread failing to schedule on a closed loop is harmless and must not print a trace.
        try:
            loop.call_soon_threadsafe(stop.set)
        except RuntimeError:
            return

    # A daemon thread rather than asyncio.to_thread: a blocked stdin read must never keep the
    # process alive, and an executor thread would (asyncio.run waits for the executor).
    threading.Thread(target=watch, name="waggle-echo-stdin", daemon=True).start()
