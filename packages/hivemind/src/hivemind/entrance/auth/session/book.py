"""Provide SessionBook: open sessions, judge whether one is still alive, and end them.

Every session at the Hive Entrance (the Hive's one HTTP door) passes through here (ADR-0033). A
login hands ``open`` a ``SessionGrant`` (the device, its binding key, how it arrived, its network,
whether the travel lock owes a step-up) and gets the token back once; only the token's SHA-256 is
kept. ``live`` is the judgement every request starts from: the session must be open, inside
``session_ttl_hours`` (and the device's own approval) and ``idle_timeout_minutes``, and its device
still APPROVED; a session found dead is ended on the spot with the reason. The rest end sessions in
bulk: ``end_sessions`` and ``offboard`` are the sessions half of the enrolment step's
``DeviceOffboarder`` (a device that leaves APPROVED loses every session in the same step),
``end_remote`` is what the Entrance Reducer calls, and ``revalidate`` is the start-time sweep
(codingrules Appendix C: "sessions are re-validated against device state on start"). Opening a
session records ``guard.entrance_login`` (the device, the listener, the address; never the token)
and every end records ``guard.entrance_session_ended`` with its reason, right after the table
changed and before the call returns (codingrules 12).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.session``. Built by
    the Entrance's composition root over a ``SessionTable`` (normally a ``SplitSessionTable``, so
    the console's sessions stay in memory); used by login, request authentication, step-up, the
    Entrance Reducer and, as a ``DeviceOffboarder``, by enrolment. Calls into the Entrance tables
    through ``EnrolmentRecords`` and the session table.

Key invariants:
    - A token is returned once, by ``open``, and never stored, logged or raised.
    - ``live`` never returns a session that is ended, expired, idle, or whose device is not
      APPROVED or has lapsed; each of those it ends before answering.
    - Ending is idempotent: ending an ended session, or a device with none, changes nothing
      and records nothing.
    - No event carries a token, its hash, a binding key or a signature.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Sessions are bound
      to a key".
    - hivemind.entrance.enrol.deps.offboarder for the seam ``offboard`` implements.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.session.models import (
    Arrival,
    BindingKind,
    EndReason,
    Listener,
    Session,
)
from hivemind.entrance.auth.session.token import mint_token, token_hash
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import DeviceNotFoundError
from hivemind.manifest import EntranceSection
from waggle.ids import DeviceId

if TYPE_CHECKING:
    # Type-only: the store imports this package's models, so a runtime import would be a cycle.
    from hivemind.entrance.enrol.deps import EnrolmentRecords
    from hivemind.entrance.store.sessions import SessionTable

# The session end each way of leaving APPROVED causes (DeviceOffboarder's reason).
DEVICE_END_REASONS: Mapping[DeviceStatus, EndReason] = {
    DeviceStatus.LOCKED: EndReason.LOCKED,
    DeviceStatus.REVOKED: EndReason.REVOKED,
    DeviceStatus.EXPIRED: EndReason.DEVICE_EXPIRED,
}

LOGIN_KIND = "guard.entrance_login"  # A login passed both factors and opened a session.
SESSION_ENDED_KIND = "guard.entrance_session_ended"  # A session ended; its payload says why.

log = get_logger(__name__)

__all__ = [
    "DEVICE_END_REASONS",
    "LOGIN_KIND",
    "SESSION_ENDED_KIND",
    "LiveSession",
    "OpenedSession",
    "SessionBook",
    "SessionGrant",
    "SessionRules",
]


@dataclass(frozen=True, slots=True)
class SessionRules:
    """What the Hive Manifest decides about sessions (``[entrance]``).

    Attributes:
        ttl: The longest a session lives, however active (``session_ttl_hours``).
        idle_timeout: A session unused this long is dead (``idle_timeout_minutes``).
        step_up_window: How long a step-up lasts (``step_up_window_minutes``).
        request_skew: How far a signed timestamp may be from the Hive Stand's clock
            (``request_skew_s``); a nonce is remembered for twice this.
        origins: The Entrance's own origins per listener: the only ``Origin`` a browser's socket
            may open from (loopback: ``http://localhost:<port>``; remote: ``public_url``'s).
    """

    ttl: timedelta
    idle_timeout: timedelta
    step_up_window: timedelta
    request_skew: timedelta
    origins: Mapping[Listener, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a non-positive duration: each would make every session dead or never checked."""
        durations = (self.ttl, self.idle_timeout, self.step_up_window, self.request_skew)
        if any(duration <= timedelta(0) for duration in durations):
            raise ValueError("Session lifetimes, the step-up window and the skew are positive.")

    @classmethod
    def from_section(
        cls, section: EntranceSection, origins: Mapping[Listener, frozenset[str]]
    ) -> SessionRules:
        """Read the rules from ``[entrance]``.

        Args:
            section: The manifest's ``[entrance]`` section.
            origins: The Entrance's own origins per listener, which the composition root derives
                from ``bind`` and ``public_url``.

        Returns:
            The rules.
        """
        return cls(
            ttl=timedelta(hours=section.session_ttl_hours),
            idle_timeout=timedelta(minutes=section.idle_timeout_minutes),
            step_up_window=timedelta(minutes=section.step_up_window_minutes),
            request_skew=timedelta(seconds=section.request_skew_s),
            origins=origins,
        )


@dataclass(frozen=True, slots=True)
class SessionGrant:
    """What a successful login decided: whose session, bound to which key, from where.

    Attributes:
        device: The device that proved itself, APPROVED.
        binding_kind: Which kind of key will sign its requests.
        binding_key: That key, unpadded base64url.
        arrival: The listener and address the login came from.
        network: The network it came from; None when unknown.
        needs_step_up: The travel lock saw a network the device never used.
    """

    device: EnrolledDevice
    binding_kind: BindingKind
    binding_key: str
    arrival: Arrival
    network: str | None
    needs_step_up: bool = False


@dataclass(frozen=True, slots=True)
class OpenedSession:
    """A new session and its token, which the device receives once and the Entrance never keeps.

    Attributes:
        token: The bearer token; hidden from ``repr`` so it never reaches a log by accident.
        session: The session as stored.
    """

    token: str = field(repr=False)
    session: Session


@dataclass(frozen=True, slots=True)
class LiveSession:
    """A session ``live`` found alive, and its device as it stands.

    Attributes:
        session: The open session.
        device: Its device, APPROVED and not lapsed.
    """

    session: Session
    device: EnrolledDevice


class SessionBook:
    """Open, judge and end the Entrance's sessions over one session table."""

    def __init__(
        self, records: EnrolmentRecords, rules: SessionRules, tables: SessionTable
    ) -> None:
        """Build the book.

        Args:
            records: The Entrance tables (for devices), the trail, the clock and the identity.
            rules: The session lifetimes, step-up window, skew and origins.
            tables: Where sessions and nonces live; a ``SplitSessionTable`` in production.
        """
        self._records = records
        self._rules = rules
        self._tables = tables

    @property
    def records(self) -> EnrolmentRecords:
        """The Entrance tables, trail, clock and identity the book works with."""
        return self._records

    @property
    def rules(self) -> SessionRules:
        """The session rules."""
        return self._rules

    @property
    def tables(self) -> SessionTable:
        """The session table (sessions and spent nonces)."""
        return self._tables

    async def open(self, grant: SessionGrant) -> OpenedSession:
        """Open a session for a device that just logged in, and return its token once.

        Args:
            grant: What the login decided.

        Returns:
            The token and the stored session.

        Raises:
            pydantic.ValidationError: The grant cannot form a session (a device already lapsed).
        """
        now = self._records.clock.now()
        token = mint_token()
        # A session never outlives its device's approval, whatever session_ttl_hours says.
        expires_at = now + self._rules.ttl
        if grant.device.expires_at is not None:
            expires_at = min(expires_at, grant.device.expires_at)
        session = Session(
            token_hash=token_hash(token),
            device_id=grant.device.id,
            binding_kind=grant.binding_kind,
            binding_key=grant.binding_key,
            listener=grant.arrival.listener,
            address=grant.arrival.trail_address,
            network=grant.network,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            needs_step_up=grant.needs_step_up,
            # ADR-0033: the loopback-bound console keeps its sessions in memory only.
            volatile=grant.device.loopback_bound,
        )
        # Latency: one local insert (or an in-memory one for the console).
        await self._tables.put(session)
        payload: dict[str, JsonValue] = {
            "listener": session.listener.value,
            "address": session.address,
            "network": session.network,
            "binding": session.binding_kind.value,
            "needs_step_up": session.needs_step_up,
        }
        await self._record(LOGIN_KIND, grant.device.id, payload, grant.device.id)
        log.info(
            "entrance.session_opened", device_id=grant.device.id, listener=session.listener.value
        )
        return OpenedSession(token, session)

    async def live(self, hashed: str, now: datetime) -> LiveSession | None:
        """Return the session with this token hash if it is alive at ``now``, ending it if not.

        Args:
            hashed: SHA-256 of the presented token.
            now: The request's moment.

        Returns:
            The session and its device, or None when unknown, ended, or found dead (and ended).
        """
        # Latency: one local read.
        session = await self._tables.get(hashed)
        if session is None or not session.is_open:
            return None
        device = await self._device(session.device_id)
        # Dead if it ran out, or its device is gone (a damaged file), not APPROVED or lapsed.
        dead = self._lapse(session, now)
        if dead is None:
            dead = EndReason.NOT_APPROVED if device is None else _standing(device, now)
        if dead is not None or device is None:
            reason = dead or EndReason.NOT_APPROVED
            # Latency: one local session-table statement; only the call that ended it records it.
            if await self._tables.end(hashed, now, reason):
                await self._ended(session.device_id, reason, session.listener, 1)
            return None
        return LiveSession(session, device)

    async def mark_stepped_up(self, session: Session, now: datetime) -> Session | None:
        """Mark ``session`` stepped up for ``step_up_window``, clearing the travel lock's flag.

        Args:
            session: The session that just re-ran its factors.
            now: When.

        Returns:
            The session as stored, or None when it ended meanwhile.
        """
        # Latency: one local session-table statement.
        return await self._tables.step_up(session.token_hash, now + self._rules.step_up_window)

    async def logout(self, session: Session) -> bool:
        """End ``session`` because its device logged out.

        Args:
            session: The session.

        Returns:
            True when it was open.
        """
        # Latency: one local session-table statement.
        ended = await self._tables.end(
            session.token_hash, self._records.clock.now(), EndReason.LOGOUT
        )
        if ended:
            await self._ended(session.device_id, EndReason.LOGOUT, session.listener, 1)
        return ended

    async def end_sessions(self, device_id: DeviceId, reason: DeviceStatus) -> int:
        """End every session of a device that left APPROVED: the offboarder's sessions half.

        Args:
            device_id: The device.
            reason: The status it moved to: LOCKED, REVOKED or EXPIRED.

        Returns:
            How many sessions this call ended.
        """
        end_reason = DEVICE_END_REASONS.get(reason, EndReason.NOT_APPROVED)
        now = self._records.clock.now()
        # Latency: one local session-table statement.
        ended = await self._tables.end_for_device(device_id, now, end_reason)
        if ended:
            await self._ended(device_id, end_reason, None, ended)
        log.info(
            "entrance.sessions_ended", device_id=device_id, reason=end_reason.value, ended=ended
        )
        return ended

    async def offboard(self, device_id: DeviceId, reason: DeviceStatus) -> None:
        """End a device's sessions; ``DeviceOffboarder.offboard``, the sessions half.

        Args:
            device_id: The device that left APPROVED.
            reason: The status it moved to.
        """
        await self.end_sessions(device_id, reason)

    async def end_remote(self) -> int:
        """End every session opened on the remote listener (the Entrance Reducer's step).

        Returns:
            How many sessions this call ended.
        """
        now = self._records.clock.now()
        # Latency: one local session-table statement.
        ended = await self._tables.end_for_listener(Listener.REMOTE, now, EndReason.REDUCED)
        # Many devices at once: the event is about the Hive whose door narrowed.
        if ended:
            hive_id = self._records.identity.hive_id
            await self._ended(hive_id, EndReason.REDUCED, Listener.REMOTE, ended)
        return ended

    async def revalidate(self) -> int:
        """End every open session that is expired, idle, or whose device is not APPROVED.

        Run at start (codingrules Appendix C), so a restart never revives a session its device
        lost while the Entrance was down.

        Returns:
            How many sessions this call ended.
        """
        now = self._records.clock.now()
        ended = 0
        # Each open session is judged exactly as a request would judge it.
        for session in await self._tables.list_open():
            if await self.live(session.token_hash, now) is None:
                ended += 1
        return ended

    async def _ended(
        self, subject_id: str, reason: EndReason, listener: Listener | None, sessions: int
    ) -> None:
        """Record ``guard.entrance_session_ended``: whose sessions, why, where, how many."""
        payload: dict[str, JsonValue] = {
            "reason": reason.value,
            "listener": listener.value if listener is not None else None,
            "sessions": sessions,
        }
        await self._record(SESSION_ENDED_KIND, subject_id, payload, None)

    async def _record(
        self, kind: str, subject_id: str, payload: dict[str, JsonValue], actor: str | None
    ) -> None:
        """Build and record one ``guard`` event stamped now, from the Entrance's identity."""
        records = self._records
        event = records.identity.event(records.clock, kind, subject_id, payload, actor)
        # Latency: one local trail write, awaited so the record exists before the call returns.
        await records.trail.record(event)

    def _lapse(self, session: Session, now: datetime) -> EndReason | None:
        """Say whether ``session`` has run out: its absolute expiry, or its idle timeout."""
        if now >= session.expires_at:
            return EndReason.EXPIRED
        if now >= session.last_seen_at + self._rules.idle_timeout:
            return EndReason.IDLE
        return None

    async def _device(self, device_id: DeviceId) -> EnrolledDevice | None:
        """Read a session's device; None if the record is gone (a damaged file)."""
        try:
            # Latency: one local primary-key read.
            return await self._records.store.get_device(device_id)
        except DeviceNotFoundError:
            return None


def _standing(device: EnrolledDevice, now: datetime) -> EndReason | None:
    """Say why a device can hold no session at ``now``, or None when it is APPROVED and current."""
    if device.status is not DeviceStatus.APPROVED:
        return DEVICE_END_REASONS.get(device.status, EndReason.NOT_APPROVED)
    # The expiry sweep may not have run yet; an approval past its expiry admits nothing anyway.
    if device.expires_at is not None and device.expires_at <= now:
        return EndReason.DEVICE_EXPIRED
    return None
