"""Provide MemorySessionTable: sessions and spent nonces in dicts, for tests, demos and the console.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality, and this one is
also production code: the Hive Stand console's sessions are volatile by design (ADR-0041: the
console "keeps its sessions in memory only"), so ``SplitSessionTable`` keeps them here and they
end with the process. It applies exactly what ``SqliteSessionTable`` applies, so the contract suite
runs unchanged over both: a session is put open and once, an ended session is never touched again,
and a nonce is claimed once until it expires.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.sessions``. Held
    by ``MemoryEntranceStore`` and by ``SplitSessionTable`` for the console's sessions. Calls into
    the protocol's rule and ``hivemind.entrance.auth.session.models``.

Key invariants:
    - Every method holds the lock for its whole body, so each read-decide-write is atomic.
    - Given ``known_device``, a session for a device the Entrance never enrolled is refused,
      exactly as the SQLite table's foreign key refuses it.
    - Behaves exactly like SqliteSessionTable under the Entrance store contract suite.

See Also:
    - hivemind.entrance.store.sessions.protocol for SessionTable.
    - hivemind.entrance.store.sessions.split for the console's routing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.session.models import EndReason, Listener, NonceClaim, Session
from hivemind.entrance.store.sessions.protocol import check_new_session
from waggle.ids import DeviceId

__all__ = ["MemorySessionTable"]


class MemorySessionTable:
    """Sessions by token hash and spent nonces by value, gone when the process exits."""

    def __init__(self, known_device: Callable[[DeviceId], bool] | None = None) -> None:
        """Start with no sessions and no spent nonces.

        Args:
            known_device: Says whether a device is enrolled, so a session for an unknown one is
                refused as the SQLite table's foreign key refuses it; None (the console's own
                table) checks nothing.
        """
        self._known_device = known_device
        self._sessions: dict[str, Session] = {}
        self._nonces: dict[str, datetime] = {}
        # Serialises every method, so each read-decide-write step is atomic (module docstring).
        self._lock = asyncio.Lock()

    async def put(self, session: Session) -> None:
        """Record a new, open session; see SessionTable.put."""
        check_new_session(session)
        async with self._lock:
            known = self._known_device is None or self._known_device(session.device_id)
            if session.token_hash in self._sessions or not known:
                raise InvariantViolationError(
                    f"Cannot record device {session.device_id}'s session: its token hash is "
                    "taken or its device is unknown."
                )
            self._sessions[session.token_hash] = session

    async def get(self, token_hash: str) -> Session | None:
        """Return one session; see SessionTable.get."""
        async with self._lock:
            return self._sessions.get(token_hash)

    async def touch(self, token_hash: str, at: datetime) -> None:
        """Move last_seen_at forward; see SessionTable.touch."""
        async with self._lock:
            current = self._open(token_hash)
            # Only forward: two requests finishing out of order never move it back.
            if current is not None and at > current.last_seen_at:
                self._sessions[token_hash] = _changed(current, last_seen_at=at)

    async def step_up(self, token_hash: str, until: datetime) -> Session | None:
        """Mark a session stepped up; see SessionTable.step_up."""
        async with self._lock:
            current = self._open(token_hash)
            if current is None:
                return None
            updated = _changed(current, stepped_up_until=until, needs_step_up=False)
            self._sessions[token_hash] = updated
            return updated

    async def end(self, token_hash: str, at: datetime, reason: EndReason) -> bool:
        """End one session; see SessionTable.end."""
        async with self._lock:
            ended = self._end_where(at, reason, lambda session: session.token_hash == token_hash)
        return ended == 1

    async def end_for_device(self, device_id: DeviceId, at: datetime, reason: EndReason) -> int:
        """End a device's sessions; see SessionTable.end_for_device."""
        async with self._lock:
            return self._end_where(at, reason, lambda session: session.device_id == device_id)

    async def end_for_listener(self, listener: Listener, at: datetime, reason: EndReason) -> int:
        """End a listener's sessions; see SessionTable.end_for_listener."""
        async with self._lock:
            return self._end_where(at, reason, lambda session: session.listener is listener)

    async def list_open(self) -> tuple[Session, ...]:
        """Return every open session, oldest first; see SessionTable.list_open."""
        async with self._lock:
            sessions = [session for session in self._sessions.values() if session.is_open]
        sessions.sort(key=lambda session: (session.created_at, session.token_hash))
        return tuple(sessions)

    async def claim_nonce(self, claim: NonceClaim) -> bool:
        """Spend a nonce once until it expires; see SessionTable.claim_nonce."""
        async with self._lock:
            # Forget what has expired first, so memory holds at most two skew windows of nonces.
            expired = [nonce for nonce, until in self._nonces.items() if until <= claim.now]
            for nonce in expired:
                del self._nonces[nonce]
            if claim.nonce in self._nonces:
                return False
            self._nonces[claim.nonce] = claim.expires_at
            return True

    def _open(self, token_hash: str) -> Session | None:
        """Return the open session with this hash, or None; the caller holds the lock."""
        session = self._sessions.get(token_hash)
        return session if session is not None and session.is_open else None

    def _end_where(
        self, at: datetime, reason: EndReason, matches: Callable[[Session], bool]
    ) -> int:
        """End every open session ``matches`` selects; the caller holds the lock."""
        ended = 0
        # Every open session the selector names is ended with the same moment and reason.
        for token_hash, session in list(self._sessions.items()):
            if session.is_open and matches(session):
                self._sessions[token_hash] = _changed(session, ended_at=at, end_reason=reason)
                ended += 1
        return ended


def _changed(session: Session, **changes: object) -> Session:
    """Return ``session`` with ``changes``, re-validated (model_validate, not model_copy)."""
    return Session.model_validate({**dict(session), **changes})
