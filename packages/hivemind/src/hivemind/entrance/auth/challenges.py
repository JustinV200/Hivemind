"""Provide ChallengeBook: single-use, short-lived challenges for the Entrance's ceremonies.

A ceremony at the Hive Entrance (the Hive's one HTTP door) proves possession of a key over fresh
random bytes the Entrance chose: a browser's passkey registration when it redeems an invite, and,
from roadmap step 10.5e, every login (ADR-0033: 32 random bytes, single use, short-lived). The
``ChallengeBook`` is where those bytes wait between being handed out and being answered. Each
``Challenge`` is bound to a subject (an invite's code hash while enrolling, a device id at login)
and optionally to a binding public key (a browser registers its session-binding key with its
login challenge, which ties that key to the ceremony), and can be taken exactly once before it
expires. The book is deliberately in memory: a restart only forces a new ceremony, and nothing
in it is worth persisting. Because issuing a challenge is an unauthenticated request, the book is
bounded: expired challenges are dropped lazily on every issue, and past ``MAX_OPEN_CHALLENGES``
the oldest open one is evicted, so a flood can cost memory up to that cap and never beyond it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Held by the
    enrolment dependencies (``hivemind.entrance.enrol.deps``) for passkey redemption and, later,
    by the login routes; built by the Entrance's composition root with its clock. Calls into
    ``hivemind.entrance.auth.canonical`` (nonces), ``hivemind.entrance.errors`` and waggle's
    clock only.

Key invariants:
    - A challenge is taken at most once: ``take`` removes it before checking anything, so even a
      refused answer spends it.
    - The book never holds more than its capacity; the oldest open challenge goes first.
    - ``take`` refuses with one ``ChallengeRejectedError`` whatever was wrong (unknown, spent,
      expired, another subject, another binding key), so it is never an oracle.
    - Synchronous and never awaiting, so each call is atomic on the event loop without a lock.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the ceremonies.
    - hivemind.entrance.enrol.redeem for the passkey redemption that takes these challenges.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.entrance.auth.canonical import NONCE_BYTES, b64url_decode, new_nonce
from hivemind.entrance.errors import ChallengeRejectedError
from waggle.clock import Clock

# More open ceremonies than any real Hive ever has at once (a handful of devices, each with one
# ceremony in flight), and small enough that a flood of unauthenticated requests costs well under
# a megabyte before the oldest challenges start being evicted.
MAX_OPEN_CHALLENGES = 1_024

__all__ = ["MAX_OPEN_CHALLENGES", "Challenge", "ChallengeBook"]


@dataclass(frozen=True, slots=True)
class Challenge:
    """One open challenge: the random bytes a ceremony must answer, and what it is bound to.

    Attributes:
        nonce: The challenge's ``NONCE_BYTES`` random bytes as unpadded base64url; its key in the
            book, and exactly what a WebAuthn ``clientDataJSON`` reports back.
        subject: What it was issued for: an invite's code hash, or a device id at login.
        binding_key: The public key the ceremony is bound to, or None when it is bound to none.
        expires_at: When it stops being answerable.
    """

    nonce: str
    subject: str
    binding_key: str | None
    expires_at: datetime

    @property
    def challenge_bytes(self) -> bytes:
        """The raw random bytes, as a ceremony signs or a WebAuthn option carries them.

        Returns:
            The ``NONCE_BYTES`` bytes behind ``nonce``.
        """
        return b64url_decode(self.nonce)


class ChallengeBook:
    """Mint challenges bound to a subject, and take each one back once, before it expires."""

    def __init__(self, clock: Clock, ttl: timedelta, capacity: int = MAX_OPEN_CHALLENGES) -> None:
        """Build an empty book.

        Args:
            clock: Stamps each challenge's expiry and decides when one has lapsed.
            ttl: How long a challenge stays answerable; must be > 0. A login uses 60 seconds
                (ADR-0033); a passkey registration covers a person setting one up (two minutes).
            capacity: The most open challenges kept at once; must be >= 1.

        Raises:
            ValueError: ``ttl`` is not positive, or ``capacity`` is below 1.
        """
        if ttl <= timedelta(0) or capacity < 1:
            raise ValueError("A challenge book needs a positive lifetime and a capacity of >= 1.")
        self._clock = clock
        self._ttl = ttl
        self._capacity = capacity
        # Insertion order is issue order, and every challenge lives the same ttl, so the first
        # entry is always both the oldest and the first to expire (see _sweep and _evict).
        self._open: dict[str, Challenge] = {}

    def issue(self, subject: str, binding_key: str | None = None) -> Challenge:
        """Mint a fresh challenge for ``subject``, optionally bound to ``binding_key``.

        Args:
            subject: What the ceremony is for: an invite's code hash, or a device id. Non-empty.
            binding_key: The public key the answer must come with, if the ceremony binds one.

        Returns:
            The new challenge; hand its ``nonce`` (or ``challenge_bytes``) to the device.

        Raises:
            ValueError: ``subject`` is empty.
        """
        if not subject:
            raise ValueError("A challenge must be issued for a subject.")
        now = self._clock.now()
        self._sweep(now)
        challenge = Challenge(
            nonce=new_nonce(NONCE_BYTES),
            subject=subject,
            binding_key=binding_key,
            expires_at=now + self._ttl,
        )
        self._open[challenge.nonce] = challenge
        self._evict()
        return challenge

    def take(self, nonce: str, subject: str, binding_key: str | None = None) -> Challenge:
        """Spend the challenge ``nonce``, which must be open, unexpired and bound as given.

        Args:
            nonce: The challenge being answered, as issued (unpadded base64url).
            subject: What the answer claims it is for; must equal the challenge's subject.
            binding_key: The key the answer comes with; must equal the challenge's binding key.

        Returns:
            The challenge, now spent.

        Raises:
            ChallengeRejectedError: Unknown, spent, expired, or bound to another subject or key;
                the challenge is spent either way.
        """
        # Removed before any check: single use holds even for an answer that is then refused.
        challenge = self._open.pop(nonce, None)
        if challenge is None or self._clock.now() >= challenge.expires_at:
            raise ChallengeRejectedError()
        if challenge.subject != subject or challenge.binding_key != binding_key:
            raise ChallengeRejectedError()
        return challenge

    def __len__(self) -> int:
        """Return how many challenges the book holds, lapsed ones not yet swept included."""
        return len(self._open)

    def _sweep(self, now: datetime) -> None:
        """Drop every lapsed challenge from the front of the book (they are in expiry order)."""
        # Stops at the first live one: everything after it was issued later, so lives longer.
        while self._open:
            oldest = next(iter(self._open.values()))
            if oldest.expires_at > now:
                return
            del self._open[oldest.nonce]

    def _evict(self) -> None:
        """Drop the oldest open challenges until the book is back within its capacity."""
        while len(self._open) > self._capacity:
            del self._open[next(iter(self._open))]
