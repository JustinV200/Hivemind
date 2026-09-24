"""Hold the Entrance's in-memory limits: rate limits, and the denial burst that locks a device.

The Hive Entrance (the Hive's one HTTP door) bounds how hard anything may knock (ADR-0033):
``rate`` is ``RateLimiter``, token buckets per device (``rate_limit_per_device``), per address
(``rate_limit_per_address``) and per device's seconds of audio (``[entrance.voice]
audio_seconds_per_minute``); ``denials`` is ``DenialCounter``, which locks a device whose
capability denials burst past ``lockout_denials`` inside ``lockout_denial_window_s``. Both are in
memory by design (each module says why) and bounded, least recently used first.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Built by the
    Entrance's composition root; called by the Landing Board's dependencies and the login flow.
    Calls into ``hivemind.entrance.enrol`` (``lock``) and waggle's clock.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Lockout, rate
      limits, travel lock".

Public API:
    - RateLimiter, MAX_TRACKED_KEYS, DEFAULT_AUDIO_S_PER_MINUTE: token buckets per device, per
      address and per device's audio seconds (rate).
    - DenialCounter, MAX_TRACKED_DEVICES, LOCKING_ACTOR: the denial-burst lock (denials).
"""

from hivemind.entrance.auth.limits.denials import LOCKING_ACTOR, MAX_TRACKED_DEVICES, DenialCounter
from hivemind.entrance.auth.limits.rate import (
    DEFAULT_AUDIO_S_PER_MINUTE,
    MAX_TRACKED_KEYS,
    RateLimiter,
)

__all__ = [
    "DEFAULT_AUDIO_S_PER_MINUTE",
    "LOCKING_ACTOR",
    "MAX_TRACKED_DEVICES",
    "MAX_TRACKED_KEYS",
    "DenialCounter",
    "RateLimiter",
]
