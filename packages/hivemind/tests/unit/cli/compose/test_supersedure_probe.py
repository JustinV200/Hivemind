"""Test the held candidate's probe of the old Queen: a listening address answers, a closed one not.

A candidate lifts its hold after the grace window only when the old Queen no longer answers
(ADR-0065), so a probe that said "gone" for a live Queen would let two Queens run.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests hivemind.cli.compose.hive.supersedure.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_supersedure.py for the hold in a running Hive.
"""

from __future__ import annotations

import asyncio

from hivemind.cli.compose.hive.supersedure import TcpOldQueen


async def test_the_probe_answers_only_while_the_old_queen_listens() -> None:
    async def accept(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    probe = TcpOldQueen(f"ws://127.0.0.1:{port}")
    listening = await probe.reachable()
    server.close()
    await server.wait_closed()

    assert listening
    assert not await probe.reachable()
    assert not await TcpOldQueen("").reachable()
