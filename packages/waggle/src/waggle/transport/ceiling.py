"""Count a connection's frames against a token bucket, so a flooding peer is noticed and stopped.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) moves one
Envelope per frame, and every frame a listener accepts costs it work: a parse, a signature check,
a hand-off to a receiver. A node is one process with its own signing key, and a Warden (the
always-on supervisor of one Cell) or a Pollen Packet (the thin gateway on an enrolled device)
that has gone wrong, or been taken over, can send frames as fast as the link carries them. The
ceiling is lax on purpose: ``max_frames_per_minute`` is a hundred a second by default, far above
any heartbeat and result traffic, with a burst allowance for the moment a reconnecting node
replays its outbox (spec section 10). Under it nothing changes; over it the listener closes the
connection (code 4029) and the node reconnects with its usual backoff. The bucket is pure: the
caller passes ``at``, read from the injected Clock, so a test drives it with a FakeClock.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.transport.limits, one bucket per
    accepted connection; calls into the standard library only.

Key invariants:
    - A fresh bucket is full: a new connection may burst ``burst`` frames at once.
    - Time only refills the bucket (up to ``burst``); a clock that steps backwards refills
      nothing and never drains it, and the bucket carries on from the new reading.
    - A refused frame costs nothing: tokens are spent only by admitted frames.
    - ``admit`` never reads a clock and never does I/O.

See Also:
    - docs/waggle/spec.md section 9 (close code 4029 and the ceiling).
    - .claude/roadmap.md step 11.3a for the decision and its defaults.
    - waggle.transport.replay for the other limit a listener applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_FRAMES_PER_MINUTE = 6000  # A hundred a second: well above any Warden's heartbeat and results.
BURST_FRAMES = 600  # Six seconds at full rate: an outbox replay after a reconnect fits inside.
_SECONDS_PER_MINUTE = 60.0  # Converts the per-minute setting to the bucket's per-second refill.

__all__ = ["BURST_FRAMES", "MAX_FRAMES_PER_MINUTE", "CeilingLimits", "FrameCeiling"]


@dataclass(frozen=True, slots=True)
class CeilingLimits:
    """How fast one connection may send, and how big a burst it gets.

    Attributes:
        max_frames_per_minute: The sustained rate the bucket refills at; must be at least 1.
        burst: The bucket's capacity, and the number of frames a fresh connection may send at
            once; must be at least 1.
    """

    max_frames_per_minute: int = MAX_FRAMES_PER_MINUTE
    burst: int = BURST_FRAMES

    def __post_init__(self) -> None:
        """Refuse a rate or a burst that would refuse every frame.

        Raises:
            ValueError: ``max_frames_per_minute`` or ``burst`` is below 1.
        """
        # Either at zero would close every connection on its first frame; "no ceiling" is spelled
        # limits.ceiling = None, so a zero here can only be a mistake.
        if self.max_frames_per_minute < 1:
            raise ValueError(
                f"max_frames_per_minute must be at least 1, got {self.max_frames_per_minute}."
            )
        if self.burst < 1:
            raise ValueError(f"burst must be at least 1, got {self.burst}.")


DEFAULT_CEILING_LIMITS = CeilingLimits()  # One frozen instance; FrameCeiling's own default.


class FrameCeiling:
    """A token bucket for one connection: ``admit`` spends a token per frame, time refills them.

    Concurrency model: not locked. ``admit`` is synchronous and awaits nothing, so the one task
    that receives on a connection is its only caller.
    """

    def __init__(self, limits: CeilingLimits = DEFAULT_CEILING_LIMITS) -> None:
        """Create a full bucket.

        Args:
            limits: The refill rate and the capacity.
        """
        self._capacity = float(limits.burst)
        self._tokens_per_second = limits.max_frames_per_minute / _SECONDS_PER_MINUTE
        self._tokens = self._capacity  # Full: a new connection may burst at once.
        # None until the first frame: the bucket starts full, so there is no elapsed time to
        # credit, and it needs no start time handed to the constructor.
        self._last: datetime | None = None

    def admit(self, at: datetime) -> bool:
        """Spend one token for a frame received at ``at``, if the bucket has one.

        Args:
            at: When the frame was received, timezone-aware as ``Clock.now()`` returns it.

        Returns:
            True when the frame is within the ceiling; False when the bucket is empty, which is
            the caller's cue to refuse the connection. A refused frame spends nothing.
        """
        # Credit the time since the previous frame, capped at the capacity. A negative gap (the
        # wall clock stepped back) credits nothing, and `_last` still moves to the new reading
        # so refilling resumes from it instead of waiting for the old time to come round again.
        if self._last is not None:
            elapsed_s = max(0.0, (at - self._last).total_seconds())
            self._tokens = min(self._capacity, self._tokens + elapsed_s * self._tokens_per_second)
        self._last = at
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True
