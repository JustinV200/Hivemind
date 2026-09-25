"""Provide SqliteSessionTable: sessions and spent nonces in the Entrance tables.

The durable ``SessionTable``, over ``entrance_sessions`` and ``entrance_nonces`` (migration 0002)
in the Hive's own database file, through the connection every Entrance table shares
(``SqliteLink``). Sessions are rows of plain columns rather than a JSON body because every
authenticated request touches one (``last_seen_at``) and spends a nonce, and a column update needs
no decode. Nonces are persisted, so a restart of ``hive serve`` does not reopen a replay window
(ADR-0041); each claim first deletes every nonce past its expiry, in the same transaction, so the
table never holds more than two skew windows of them. The Hive Stand console's sessions are never
written here: ``put`` refuses a volatile session outright.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.sessions``. Built
    by ``SqliteEntranceStore`` over its link. Calls into ``hivemind.common.sqlite`` (transactions),
    the protocol's rule and ``hivemind.entrance.auth.session.models``.

Key invariants:
    - Every update names ``ended_at IS NULL``, so an ended session is never changed again.
    - A nonce claim is one transaction: purge, then insert-if-absent; of two claims of one nonce
      exactly one inserts.
    - A volatile (console) session never reaches this table.

See Also:
    - hivemind.entrance.store.sessions.protocol for SessionTable.
    - hivemind.entrance.store.migrations for the 0002 tables.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import transaction
from hivemind.entrance.auth.session.models import EndReason, Listener, NonceClaim, Session
from hivemind.entrance.store.link import SqliteLink
from hivemind.entrance.store.sessions.protocol import check_new_session
from waggle.ids import DeviceId

# Plain literals, not f-strings, so ruff's S608 heuristic has nothing to flag; _row and the
# SELECTs below list the same fourteen columns in the same order.
_INSERT_SQL = """
INSERT INTO entrance_sessions (token_hash, device_id, binding_kind, binding_key, listener, address,
    network, created_at, last_seen_at, expires_at, stepped_up_until, needs_step_up, ended_at,
    end_reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_SQL = """
SELECT token_hash, device_id, binding_kind, binding_key, listener, address, network, created_at,
    last_seen_at, expires_at, stepped_up_until, needs_step_up, ended_at, end_reason
FROM entrance_sessions WHERE token_hash = ?
"""
_OPEN_SQL = """
SELECT token_hash, device_id, binding_kind, binding_key, listener, address, network, created_at,
    last_seen_at, expires_at, stepped_up_until, needs_step_up, ended_at, end_reason
FROM entrance_sessions WHERE ended_at IS NULL ORDER BY created_at, token_hash
"""
_TOUCH_SQL = (
    "UPDATE entrance_sessions SET last_seen_at = ? "
    "WHERE token_hash = ? AND ended_at IS NULL AND last_seen_at < ?"
)
_STEP_UP_SQL = (
    "UPDATE entrance_sessions SET stepped_up_until = ?, needs_step_up = 0 "
    "WHERE token_hash = ? AND ended_at IS NULL"
)
_END_ONE_SQL = (
    "UPDATE entrance_sessions SET ended_at = ?, end_reason = ? "
    "WHERE token_hash = ? AND ended_at IS NULL"
)
_END_DEVICE_SQL = (
    "UPDATE entrance_sessions SET ended_at = ?, end_reason = ? "
    "WHERE device_id = ? AND ended_at IS NULL"
)
_END_LISTENER_SQL = (
    "UPDATE entrance_sessions SET ended_at = ?, end_reason = ? "
    "WHERE listener = ? AND ended_at IS NULL"
)
_PURGE_NONCES_SQL = "DELETE FROM entrance_nonces WHERE expires_at <= ?"
_CLAIM_NONCE_SQL = (
    "INSERT INTO entrance_nonces (nonce, token_hash, expires_at) VALUES (?, ?, ?) "
    "ON CONFLICT (nonce) DO NOTHING"
)

__all__ = ["SqliteSessionTable"]


class SqliteSessionTable:
    """The durable SessionTable: two tables, written through the Entrance's one connection."""

    def __init__(self, link: SqliteLink) -> None:
        """Wrap the shared link; ``SqliteEntranceStore`` builds this once.

        Args:
            link: The Entrance tables' connection, thread and lock.
        """
        self._link = link

    async def put(self, session: Session) -> None:
        """Record a new, open, durable session; see SessionTable.put."""
        check_new_session(session)
        # ADR-0041: the console "keeps its sessions in memory only"; a durable table refuses one.
        if session.volatile:
            raise InvariantViolationError("A volatile (console) session is never persisted.")
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        await self._link.run(_insert, session)

    async def get(self, token_hash: str) -> Session | None:
        """Return one session; see SessionTable.get."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        row = await self._link.run(_fetch_one, _SELECT_SQL, (token_hash,))
        return _session(row) if row is not None else None

    async def touch(self, token_hash: str, at: datetime) -> None:
        """Move last_seen_at forward; see SessionTable.touch."""
        stamp = at.isoformat()
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        await self._link.run(_write, _TOUCH_SQL, (stamp, token_hash, stamp))

    async def step_up(self, token_hash: str, until: datetime) -> Session | None:
        """Mark a session stepped up; see SessionTable.step_up."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        changed = await self._link.run(_write, _STEP_UP_SQL, (until.isoformat(), token_hash))
        return await self.get(token_hash) if changed else None

    async def end(self, token_hash: str, at: datetime, reason: EndReason) -> bool:
        """End one session; see SessionTable.end."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        ended = await self._link.run(_write, _END_ONE_SQL, _end_args(at, reason, token_hash))
        return ended == 1

    async def end_for_device(self, device_id: DeviceId, at: datetime, reason: EndReason) -> int:
        """End a device's sessions; see SessionTable.end_for_device."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        return await self._link.run(_write, _END_DEVICE_SQL, _end_args(at, reason, device_id))

    async def end_for_listener(self, listener: Listener, at: datetime, reason: EndReason) -> int:
        """End a listener's sessions; see SessionTable.end_for_listener."""
        args = _end_args(at, reason, listener.value)
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        return await self._link.run(_write, _END_LISTENER_SQL, args)

    async def list_open(self) -> tuple[Session, ...]:
        """Return every open session, oldest first; see SessionTable.list_open."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        rows = await self._link.run(_fetch_all, _OPEN_SQL, ())
        return tuple(_session(row) for row in rows)

    async def claim_nonce(self, claim: NonceClaim) -> bool:
        """Spend a nonce once until it expires; see SessionTable.claim_nonce."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        return await self._link.run(_claim, claim)


# ──────────────────────────────────────────────────────────────────────────────
# Blocking bodies: each runs on the link's connection thread, never on the event loop.
# ──────────────────────────────────────────────────────────────────────────────


def _insert(connection: sqlite3.Connection, session: Session) -> None:
    """Insert one session row; a taken token hash is an invariant broken, not a retry."""
    try:
        with transaction(connection):
            connection.execute(_INSERT_SQL, _row(session))
    except sqlite3.IntegrityError as exc:
        raise InvariantViolationError(
            f"Cannot record device {session.device_id}'s session: its token hash is taken or "
            "its device is unknown."
        ) from exc


def _fetch_one(
    connection: sqlite3.Connection, sql: str, params: tuple[str, ...]
) -> sqlite3.Row | None:
    """Run one read and return its first row."""
    row: sqlite3.Row | None = connection.execute(sql, params).fetchone()
    return row


def _fetch_all(
    connection: sqlite3.Connection, sql: str, params: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Run one read and return every row."""
    return connection.execute(sql, params).fetchall()


def _write(connection: sqlite3.Connection, sql: str, params: tuple[str, ...]) -> int:
    """Run one guarded update in its own transaction and return how many rows it changed."""
    with transaction(connection):
        return connection.execute(sql, params).rowcount


def _claim(connection: sqlite3.Connection, claim: NonceClaim) -> bool:
    """Purge expired nonces and insert this one if absent, in one transaction."""
    with transaction(connection):
        connection.execute(_PURGE_NONCES_SQL, (claim.now.isoformat(),))
        params = (claim.nonce, claim.token_hash, claim.expires_at.isoformat())
        return connection.execute(_CLAIM_NONCE_SQL, params).rowcount == 1


def _end_args(at: datetime, reason: EndReason, key: str) -> tuple[str, str, str]:
    """Return the parameters of an end statement: when, why, and whose."""
    return (at.isoformat(), reason.value, key)


def _row(session: Session) -> tuple[str | int | None, ...]:
    """Return a session's column values in the order ``_INSERT_SQL`` names them."""
    return (
        session.token_hash,
        session.device_id,
        session.binding_kind.value,
        session.binding_key,
        session.listener.value,
        session.address,
        session.network,
        session.created_at.isoformat(),
        session.last_seen_at.isoformat(),
        session.expires_at.isoformat(),
        _iso(session.stepped_up_until),
        int(session.needs_step_up),
        _iso(session.ended_at),
        session.end_reason.value if session.end_reason is not None else None,
    )


def _session(row: sqlite3.Row) -> Session:
    """Rebuild a Session from its row, validating it like any other."""
    fields = dict(row)
    fields["needs_step_up"] = bool(fields["needs_step_up"])
    return Session.model_validate(fields)


def _iso(moment: datetime | None) -> str | None:
    """Render an optional timestamp for a column."""
    return moment.isoformat() if moment is not None else None
