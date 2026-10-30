"""Provide RateLimiter and ByteBudget: token buckets per device and address, bounded in memory.

The Hive Entrance (the Hive's one HTTP door) limits how often one device, and one network address,
may knock (ADR-0041): ``rate_limit_per_device`` requests a minute per enrolled device, and
``rate_limit_per_address`` per address, which also bounds every unauthenticated route (enrolment,
login challenges). Each key has a token bucket holding up to a minute's allowance and refilling at
that rate, so a device may burst a minute's worth and then keeps its steady rate. An invalid login
proof is charged to its address once more (``charge_address``), so garbage logins drain an
address's allowance faster and never touch the device they name (device ids are not secret).
Audio is a separate budget per device (roadmap step 10.5f, ``[entrance.voice]
audio_seconds_per_minute``): a third family of buckets counted in seconds of audio, not requests,
and charged a clip's whole length before it is transcribed (``allow_audio``), so a device may send
at most that much audio a minute however few requests carry it. A clip still costs one request
token besides, like every other request. Images (roadmap step 12.12) have the same kind of budget
in bytes, ``ByteBudget`` (``[entrance] image_bytes_per_minute``), held by the image intake's
services rather than here, so this limiter's constructor stays small.

The buckets are **in memory by design**: they describe the last minute and nothing older, so
persisting them would cost a write on every request to protect a minute that a restart only
restarts; what they lead to that matters (a lockout, a reduction) is persisted where it happens.
Memory is bounded: past ``capacity`` keys per family the least recently used bucket is evicted,
so a flood of addresses costs at most that many buckets (a flood from that many addresses is the
Guard Bee's and the Entrance Reducer's to answer, not a per-address bucket's).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.limits``. Built
    once by the Entrance's composition root; called by the Landing Board's dependencies on every
    request (later steps) and by the login flow. Calls into waggle's clock only.

Key invariants:
    - At most ``capacity`` buckets per family are held; eviction takes the least recently used.
    - A bucket never holds more than one minute's allowance (for audio: a minute's seconds).
    - Synchronous and never awaiting, so each call is atomic on the event loop without a lock.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Lockout, rate
      limits, travel lock".
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from hivemind.manifest import EntranceSection
from waggle.clock import Clock

MAX_TRACKED_KEYS = 4_096  # Buckets kept per family: far more devices than any Hive has.
_SECONDS_PER_MINUTE = 60.0  # The manifest's limits are per minute.
_REQUEST_COST = 1.0  # One request, or one extra charge, takes one token.
DEFAULT_AUDIO_S_PER_MINUTE = 120.0  # [entrance.voice] audio_seconds_per_minute's own default.
_STAND_BUCKET = "stand:"  # Prefixes a stand-key holder's address: a bucket of its own (ADR-0068).

__all__ = [
    "DEFAULT_AUDIO_S_PER_MINUTE",
    "MAX_TRACKED_KEYS",
    "ByteBudget",
    "QuestionBudget",
    "RateLimiter",
    "address_bucket",
]


def address_bucket(address: str, *, stand: bool) -> str:
    """Name the per-address bucket a request is charged to.

    A request carrying the stand key (ADR-0068) is the Hive Stand's own, so it is counted apart
    from everything else on its address: a Virtual Cell's guest flooding ``127.0.0.1`` cannot
    starve the operator's console.

    Args:
        address: The peer address the listener reported.
        stand: Whether the request carried this boot's stand key.

    Returns:
        The bucket's key: the address, or the address under the stand prefix.
    """
    return _STAND_BUCKET + address if stand else address


@dataclass(slots=True)
class _Bucket:
    """One key's tokens, and the monotonic moment they were last refilled."""

    tokens: float
    refilled_at: float


class _Buckets:
    """One family of token buckets (per device, per address, per audio or image budget).

    Owns its map, which is bounded in keys; synchronous and never awaiting, so each ``take`` is
    atomic on the event loop without a lock.
    """

    def __init__(self, clock: Clock, per_minute: float, capacity: int) -> None:
        """Build a family with no buckets yet.

        Args:
            clock: Its ``monotonic`` reading refills the buckets.
            per_minute: What a full bucket holds, and what it refills in a minute; > 0.
            capacity: Buckets kept before the least recently used is evicted; >= 1.
        """
        self._clock = clock
        self._per_minute = per_minute
        self._capacity = capacity
        # Insertion order is recency order: every take moves its key to the end.
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def take(self, key: str, cost: float = _REQUEST_COST) -> bool:
        """Refill ``key``'s bucket for the time passed, then take ``cost`` tokens if it has them.

        Args:
            key: The device or address the bucket belongs to.
            cost: How many tokens to take; a refused take takes nothing.

        Returns:
            True when the bucket had ``cost`` tokens; False when the key is over its rate.
        """
        now = self._clock.monotonic()
        per_minute = self._per_minute
        bucket = self._buckets.pop(key, None)
        # A key seen for the first time (or evicted since) starts with a full minute's allowance.
        if bucket is None:
            bucket = _Bucket(tokens=per_minute, refilled_at=now)
        elapsed = max(0.0, now - bucket.refilled_at)
        bucket.tokens = min(per_minute, bucket.tokens + elapsed * per_minute / _SECONDS_PER_MINUTE)
        bucket.refilled_at = now
        allowed = bucket.tokens >= cost
        if allowed:
            bucket.tokens -= cost
        # Re-inserted at the end (most recent); the oldest go first once past capacity.
        self._buckets[key] = bucket
        while len(self._buckets) > self._capacity:
            self._buckets.popitem(last=False)
        return allowed


class ByteBudget:
    """A device's budget of bytes a minute, charged whole before anything is kept.

    The image intake's counterpart of the audio budget (``RateLimiter.allow_audio``): a token
    bucket of ``[entrance] image_bytes_per_minute`` bytes per device, so a device may send at most
    that much however few requests carry it. An image still costs one request token besides, like
    every other request.
    """

    def __init__(
        self, clock: Clock, bytes_per_minute: int, capacity: int = MAX_TRACKED_KEYS
    ) -> None:
        """Build a budget with no buckets yet.

        Args:
            clock: Its ``monotonic`` reading refills the buckets.
            bytes_per_minute: Bytes one device may send a minute; >= 1.
            capacity: Devices whose buckets are kept before the least recently used goes; >= 1.

        Raises:
            ValueError: The budget or the capacity is below 1.
        """
        if min(bytes_per_minute, capacity) < 1:
            raise ValueError("A byte budget and its bucket capacity must be at least 1.")
        self._buckets = _Buckets(clock, float(bytes_per_minute), capacity)

    @classmethod
    def from_section(cls, section: EntranceSection, clock: Clock) -> ByteBudget:
        """Build the budget from ``[entrance] image_bytes_per_minute``.

        Args:
            section: The manifest's ``[entrance]`` section.
            clock: The Entrance's clock.

        Returns:
            The budget.
        """
        return cls(clock, section.image_bytes_per_minute)

    def allow(self, device_id: str, size: int) -> bool:
        """Charge ``size`` bytes to ``device_id``, if its budget has that much left.

        Args:
            device_id: The device that sent the image, authenticated.
            size: The image's size in bytes; charged whole or not at all.

        Returns:
            True when the image may be kept; False when the device is over its budget.
        """
        return self._buckets.take(device_id, float(size))


class QuestionBudget:
    """A session's budget of help questions a minute, each a model call the Hive pays for.

    The help's Ask (ADR-0070) answers through the cheapest chat binding, which still costs seats
    and often money, so beside the per-device request rate each session is held to a few
    questions a minute: a token bucket of ``per_minute`` questions keyed by the session, held by
    the help's services as ``ByteBudget`` is by the image intake's.
    """

    def __init__(self, clock: Clock, per_minute: int, capacity: int = MAX_TRACKED_KEYS) -> None:
        """Build a budget with no buckets yet.

        Args:
            clock: Its ``monotonic`` reading refills the buckets.
            per_minute: Questions one session may ask a minute; >= 1.
            capacity: Sessions whose buckets are kept before the least recently used goes; >= 1.

        Raises:
            ValueError: The budget or the capacity is below 1.
        """
        if min(per_minute, capacity) < 1:
            raise ValueError("A question budget and its bucket capacity must be at least 1.")
        self._buckets = _Buckets(clock, float(per_minute), capacity)

    def allow(self, session_key: str) -> bool:
        """Charge one question to ``session_key``, if its budget has one left.

        Args:
            session_key: What names the session, never its token (its hash serves).

        Returns:
            True when the question may be answered; False when the session is over its budget.
        """
        return self._buckets.take(session_key)


class RateLimiter:
    """Token buckets per device, per address and per device's audio, each bounded in keys."""

    def __init__(
        self,
        clock: Clock,
        per_device: int,
        per_address: int,
        capacity: int = MAX_TRACKED_KEYS,
        audio_s_per_device: float = DEFAULT_AUDIO_S_PER_MINUTE,
    ) -> None:
        """Build a limiter with no buckets yet.

        Args:
            clock: Its ``monotonic`` reading refills the buckets.
            per_device: Requests per minute one device may make; >= 1.
            per_address: Requests per minute one address may make; >= 1.
            capacity: Buckets kept per family before the least recently used is evicted; >= 1.
            audio_s_per_device: Seconds of audio per minute one device may send; > 0.

        Raises:
            ValueError: A rate or the capacity is below 1, or the audio budget is not positive.
        """
        if min(per_device, per_address, capacity) < 1:
            raise ValueError("Rate limits and the bucket capacity must be at least 1.")
        if audio_s_per_device <= 0:
            raise ValueError("A device's audio budget must be a positive number of seconds.")
        self._devices = _Buckets(clock, float(per_device), capacity)
        self._addresses = _Buckets(clock, float(per_address), capacity)
        self._audio = _Buckets(clock, float(audio_s_per_device), capacity)

    @classmethod
    def from_section(cls, section: EntranceSection, clock: Clock) -> RateLimiter:
        """Build a limiter from ``[entrance]``'s rate limits and its voice section's audio budget.

        Args:
            section: The manifest's ``[entrance]`` section.
            clock: The Entrance's clock.

        Returns:
            The limiter, its audio budget ``[entrance.voice] audio_seconds_per_minute``.
        """
        return cls(
            clock,
            section.rate_limit_per_device,
            section.rate_limit_per_address,
            audio_s_per_device=section.voice.audio_seconds_per_minute,
        )

    def allow_device(self, device_id: str) -> bool:
        """Take one token from ``device_id``'s bucket.

        Args:
            device_id: An authenticated request's device.

        Returns:
            True when the request may proceed; False when the device is over its rate.
        """
        return self._devices.take(device_id)

    def allow_address(self, address: str) -> bool:
        """Take one token from ``address``'s bucket.

        Args:
            address: The requesting network address, as the listener reported it.

        Returns:
            True when the request may proceed; False when the address is over its rate.
        """
        return self._addresses.take(address)

    def charge_address(self, address: str) -> None:
        """Take one more token from ``address``: an invalid login proof costs it double.

        Args:
            address: The address the invalid proof came from.
        """
        self._addresses.take(address)

    def allow_audio(self, device_id: str, seconds: float) -> bool:
        """Charge ``seconds`` of audio to ``device_id``'s audio budget, if it has that much left.

        A clip is charged whole or not at all: a refused clip takes nothing, so the device may
        send a shorter one, or the same one once its bucket has refilled.

        Args:
            device_id: The device that sent the clip, authenticated.
            seconds: The clip's length, read from its header or declared; > 0.

        Returns:
            True when the clip may be transcribed; False when the device is over its budget.
        """
        return self._audio.take(device_id, seconds)
