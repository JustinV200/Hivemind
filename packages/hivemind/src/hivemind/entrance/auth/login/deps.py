"""Define AuthDeps: everything login and step-up are built from, in small frozen bundles.

Login and step-up at the Hive Entrance (the Hive's one HTTP door) touch a lot (ADR-0033): the
Entrance tables, the Pheromone Trail and the enrolment seams (a lockout goes through the enrolment
step's ``lock``), the session book, the challenge book a login is answered against, the WebAuthn
relying parties a passkey can be bound to, the password hasher (worker threads behind a
semaphore), the rate limiter an invalid proof is charged to, the lockout threshold and, when
``travel_lock`` is on, the travel lock. A constructor stays within five parameters (codingrules
5.1), so these are bundles grouped by what they are for, and one ``AuthDeps`` holds them. The
composition root builds it once.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.login``. Built by
    the Entrance's composition root or a test; passed to ``begin_login``, ``finish_login``,
    ``step_up`` and the confirmation flow. Calls into the bundles' own types only.

Key invariants:
    - Every bundle is frozen; ``LoginGuards`` refuses a lockout threshold below one.
    - The login challenge book is not enrolment's: a login challenge lives 60 seconds.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for login.
    - hivemind.entrance.enrol.deps for the enrolment bundle this one wraps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.entrance.auth.challenges import ChallengeBook
from hivemind.entrance.auth.limits.rate import RateLimiter
from hivemind.entrance.auth.passkeys import AUTHENTICATION_TIMEOUT_MS, RelyingParty
from hivemind.entrance.auth.password import PasswordHasher
from hivemind.entrance.auth.session.book import SessionBook
from hivemind.entrance.enrol.deps import EnrolmentDeps, EnrolmentRecords

if TYPE_CHECKING:
    # Type-only: the travel lock is optional, and only its instance is ever held here.
    from hivemind.entrance.auth.travel.lock import TravelLock

# ADR-0033: a login challenge is single use and lives 60 seconds (the passkey options say so too).
LOGIN_CHALLENGE_TTL = timedelta(milliseconds=AUTHENTICATION_TIMEOUT_MS)

__all__ = ["LOGIN_CHALLENGE_TTL", "AuthDeps", "LoginCeremony", "LoginGuards"]


@dataclass(frozen=True, slots=True)
class LoginCeremony:
    """What a device proof and a password are checked against.

    Attributes:
        challenges: The login (and step-up) challenges, ``LOGIN_CHALLENGE_TTL`` each.
        relying_parties: Every relying party a passkey may be bound to: ``localhost`` on the Hive
            Stand, and the remote DNS name when exposed.
        hasher: The Entrance's one password hasher, whose semaphore bounds every derivation.
    """

    challenges: ChallengeBook
    relying_parties: tuple[RelyingParty, ...]
    hasher: PasswordHasher

    def relying_party(self, rp_id: str | None) -> RelyingParty | None:
        """Return the relying party with id ``rp_id``, or None when the Entrance serves none.

        Args:
            rp_id: The relying party a device's passkey is bound to.

        Returns:
            The matching relying party, or None.
        """
        return next((party for party in self.relying_parties if party.id == rp_id), None)


@dataclass(frozen=True, slots=True)
class LoginGuards:
    """What bounds login attempts.

    Attributes:
        limiter: The per-address and per-device buckets; an invalid proof is charged to its
            address.
        lockout_attempts: Valid-proof login failures in a row that lock a device
            (``[entrance] lockout_attempts``); >= 1.
        travel: The travel lock, when ``travel_lock`` is on; None otherwise.
    """

    limiter: RateLimiter
    lockout_attempts: int
    travel: TravelLock | None = None

    def __post_init__(self) -> None:
        """Refuse a threshold that would lock a device before its first failure."""
        if self.lockout_attempts < 1:
            raise ValueError("lockout_attempts must be at least 1.")


@dataclass(frozen=True, slots=True)
class AuthDeps:
    """Everything login and step-up need, built once by the composition root.

    Attributes:
        enrolment: The Entrance tables, trail, clock, identity and seams (``lock`` needs them).
        sessions: The session book logins open sessions in.
        ceremony: The challenge book, relying parties and password hasher.
        guards: The rate limiter, the lockout threshold and the travel lock.
    """

    enrolment: EnrolmentDeps
    sessions: SessionBook
    ceremony: LoginCeremony
    guards: LoginGuards

    @property
    def records(self) -> EnrolmentRecords:
        """The Entrance tables, the trail, the clock and the identity (from ``enrolment``)."""
        return self.enrolment.records
