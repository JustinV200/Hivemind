"""Provide SplitSessionTable: the console's sessions in memory, every other session in the tables.

ADR-0033: the Hive Stand's own console "keeps its sessions in memory only", and the console
bootstrap promises that "console sessions are never persisted by anything". Every other device's
sessions are persisted, so they survive a restart and their spent nonces keep a replay window
closed.
``SplitSessionTable`` is one ``SessionTable`` over the two: it puts a volatile session (marked so
by the session book when the console logs in) in the in-memory table and every other in the durable
one, looks a token up in memory first, and applies every end to both, so no caller ever has to know
which kind of session it holds. A console session therefore ends with the process that holds it,
exactly like the console's unwrapped key.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.sessions``. Built
    by the Entrance's composition root (or a test) over ``EntranceStore.sessions`` and a fresh
    ``MemorySessionTable``; handed to ``hivemind.entrance.auth.session.SessionBook``. Calls into
    the two tables it wraps.

Key invariants:
    - A volatile session is only ever put in ``volatile``; the durable table refuses one anyway.
    - A token hash is looked up in ``volatile`` first; hashes are 256-bit digests of fresh
      tokens, so the two tables never hold the same one.

See Also:
    - hivemind.entrance.store.sessions.protocol for SessionTable.
    - hivemind.entrance.enrol.console for the console and its wrapped key.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.entrance.auth.session.models import EndReason, Listener, NonceClaim, Session
from hivemind.entrance.store.sessions.protocol import SessionTable
from waggle.ids import DeviceId

__all__ = ["SplitSessionTable"]


class SplitSessionTable:
    """One SessionTable over a durable table and a volatile one for the console's sessions."""

    def __init__(self, durable: SessionTable, volatile: SessionTable) -> None:
        """Wrap the two tables.

        Args:
            durable: The Entrance tables' own sessions (``EntranceStore.sessions``).
            volatile: An in-memory table for the console's sessions.
        """
        self._durable = durable
        self._volatile = volatile

    async def put(self, session: Session) -> None:
        """Record a session in the table its kind belongs in; see SessionTable.put."""
        table = self._volatile if session.volatile else self._durable
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        await table.put(session)

    async def get(self, token_hash: str) -> Session | None:
        """Return one session from either table; see SessionTable.get."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        found = await self._volatile.get(token_hash)
        return found if found is not None else await self._durable.get(token_hash)

    async def touch(self, token_hash: str, at: datetime) -> None:
        """Move last_seen_at forward wherever the session lives; see SessionTable.touch."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        await (await self._holder(token_hash)).touch(token_hash, at)

    async def step_up(self, token_hash: str, until: datetime) -> Session | None:
        """Mark a session stepped up wherever it lives; see SessionTable.step_up."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        return await (await self._holder(token_hash)).step_up(token_hash, until)

    async def end(self, token_hash: str, at: datetime, reason: EndReason) -> bool:
        """End one session wherever it lives; see SessionTable.end."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        return await (await self._holder(token_hash)).end(token_hash, at, reason)

    async def end_for_device(self, device_id: DeviceId, at: datetime, reason: EndReason) -> int:
        """End a device's sessions in both tables; see SessionTable.end_for_device."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        ended = await self._volatile.end_for_device(device_id, at, reason)
        return ended + await self._durable.end_for_device(device_id, at, reason)

    async def end_for_listener(self, listener: Listener, at: datetime, reason: EndReason) -> int:
        """End a listener's sessions in both tables; see SessionTable.end_for_listener."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        ended = await self._volatile.end_for_listener(listener, at, reason)
        return ended + await self._durable.end_for_listener(listener, at, reason)

    async def list_open(self) -> tuple[Session, ...]:
        """Return the open sessions of both tables, oldest first; see SessionTable.list_open."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        sessions = [*await self._volatile.list_open(), *await self._durable.list_open()]
        sessions.sort(key=lambda session: (session.created_at, session.token_hash))
        return tuple(sessions)

    async def claim_nonce(self, claim: NonceClaim) -> bool:
        """Spend a nonce in the table its session lives in; see SessionTable.claim_nonce."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        return await (await self._holder(claim.token_hash)).claim_nonce(claim)

    async def _holder(self, token_hash: str) -> SessionTable:
        """Return the table holding ``token_hash``: the volatile one if it has it, else durable."""
        # Latency: local tables only (SQLite on its thread, or memory); sub-millisecond.
        return self._volatile if await self._volatile.get(token_hash) is not None else self._durable
