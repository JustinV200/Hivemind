"""Define SessionTable: where sessions and the request nonces they have seen are kept.

A session at the Hive Entrance (the Hive's one HTTP door) is a row keyed by its token's SHA-256
(ADR-0041); every signed request spends a nonce, and a nonce seen once is refused for twice the
clock-skew window, persisted so that a restart does not reopen a replay window. ``SessionTable``
is that seam (codingrules 8.1), implemented by ``SqliteSessionTable`` (the Entrance tables, next to
the devices), ``MemorySessionTable`` (tests, demos, and the Hive Stand console's volatile sessions)
and ``SplitSessionTable``, which routes the console's sessions to memory and every other one to
the tables. The pure ``check_new_session`` is the rule every implementation applies when a
session is put.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.sessions``. Used
    by ``hivemind.entrance.auth.session`` (the session book and request authentication). Calls
    into ``hivemind.entrance.auth.session.models`` and ``hivemind.common.errors``.

Key invariants:
    - A session is put open; it ends once, and an ended session never opens again: ``touch``,
      ``step_up`` and ``end`` leave an ended row untouched.
    - ``claim_nonce`` is atomic: of two requests carrying one nonce, exactly one claims it, and
      it stays claimed until its ``expires_at`` has passed.
    - A token hash names at most one session.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for sessions.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.session.models import EndReason, Listener, NonceClaim, Session
from waggle.ids import DeviceId

__all__ = ["SessionTable", "check_new_session"]


class SessionTable(Protocol):
    """Keep sessions by token hash, and the nonces their requests have spent."""

    async def put(self, session: Session) -> None:
        """Record a new, open session.

        Args:
            session: The session a login opened.

        Raises:
            InvariantViolationError: It is already ended, its token hash is taken, or a durable
                table was handed a volatile (console) session.
        """
        ...

    async def get(self, token_hash: str) -> Session | None:
        """Return the session with this token hash, open or ended.

        Args:
            token_hash: SHA-256 of the presented token.

        Returns:
            The session, or None when no session has that hash.
        """
        ...

    async def touch(self, token_hash: str, at: datetime) -> None:
        """Move an open session's ``last_seen_at`` forward to ``at``; an ended one is left alone.

        Args:
            token_hash: The session.
            at: When it authenticated a request.
        """
        ...

    async def step_up(self, token_hash: str, until: datetime) -> Session | None:
        """Mark an open session stepped up until ``until``, clearing the travel lock's flag.

        Args:
            token_hash: The session.
            until: When its step-up window closes.

        Returns:
            The session as stored, or None when it is unknown or ended.
        """
        ...

    async def end(self, token_hash: str, at: datetime, reason: EndReason) -> bool:
        """End one open session.

        Args:
            token_hash: The session.
            at: When.
            reason: Why.

        Returns:
            True when it was open and is now ended; False when unknown or already ended.
        """
        ...

    async def end_for_device(self, device_id: DeviceId, at: datetime, reason: EndReason) -> int:
        """End every open session of one device.

        Args:
            device_id: The device.
            at: When.
            reason: Why.

        Returns:
            How many sessions this call ended.
        """
        ...

    async def end_for_listener(self, listener: Listener, at: datetime, reason: EndReason) -> int:
        """End every open session opened on one listener.

        Args:
            listener: The listener, REMOTE for the Entrance Reducer.
            at: When.
            reason: Why.

        Returns:
            How many sessions this call ended.
        """
        ...

    async def list_open(self) -> tuple[Session, ...]:
        """Return every open session, oldest first (expired and idle ones included).

        Returns:
            The sessions not yet ended, ordered by ``created_at`` then token hash.
        """
        ...

    async def claim_nonce(self, claim: NonceClaim) -> bool:
        """Spend a nonce, first forgetting every nonce whose ``expires_at`` is at or before now.

        Args:
            claim: The nonce, its session, its expiry and the request's moment.

        Returns:
            True when the nonce was fresh and is now spent; False when it was already spent.
        """
        ...


def check_new_session(session: Session) -> None:
    """Refuse to record a session that is not open.

    Args:
        session: The session ``put`` was given.

    Raises:
        InvariantViolationError: The session is already ended.
    """
    if not session.is_open:
        raise InvariantViolationError(
            f"A session is recorded open; device {session.device_id}'s is already ended."
        )
