"""Count how long the Queen has been silent: the dead-man switch's clock.

A device with no Warden (the per-Cell supervisor) on it cannot ask anyone what to do when it
loses the Queen (the Hive's central orchestrator), so a Pollen Packet (the thin gateway program
on a device the Hive borrows) releases every lease it holds once the Queen has been silent longer
than the enrolled limit. This module is that clock and nothing else: ``heard`` marks a moment the
Queen was heard from (a verified frame, or a connection kept alive by pongs), ``silence_s``
measures the gap on the *monotonic* clock, so a change of the wall clock neither trips the switch
nor delays it, and ``heard_at`` gives the wall time to persist at a coarse interval, so that
restarting the packet does not restart the clock (gateway-contract section 13). What the packet
does when the switch trips (kill the leases' processes, restore, queue ``cell.lease_released``
with cause ``DEAD_MAN``) is pollen.agent.leases' job.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Fed by pollen.agent.dispatch
    and the connection loop; read by the packet's watchdog task in pollen.agent.packet; calls
    into the injected waggle Clock only.

Key invariants:
    - Elapsed time is measured only with ``Clock.monotonic``; the wall clock is used to restore
      the gap across a restart and to stamp what is persisted, never to measure a silence.
    - A switch restored from a persisted time in the future (a clock set back) starts at zero
      silence rather than a negative one.

See Also:
    - docs/waggle/gateway-contract.md section 13 (the dead-man switch).
    - .claude/codingrules.md section 8.8 for why a device with no Warden runs one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from waggle.clock import Clock

__all__ = ["DeadManSwitch"]


class DeadManSwitch:
    """The monotonic clock of "how long since the Queen was heard".

    Owns mutable state (codingrules 8.5, documented): ``_heard_mono`` is the monotonic reading
    of the last moment the Queen was heard from, replaced by ``heard``.
    """

    def __init__(self, clock: Clock, last_heard_at: datetime | None = None) -> None:
        """Start the clock, optionally from a persisted wall-clock moment.

        Args:
            clock: The injected time source.
            last_heard_at: When the Queen was last heard before this process started, from the
                persisted state; None (a device that never connected) starts the silence at zero.
        """
        self._clock = clock
        silence = 0.0
        if last_heard_at is not None:
            # The gap since the persisted moment, converted to the monotonic scale; a persisted
            # moment ahead of the wall clock (the clock was set back) counts as no gap at all.
            silence = max(0.0, (clock.now() - last_heard_at).total_seconds())
        self._heard_mono = clock.monotonic() - silence

    def heard(self) -> None:
        """Record that the Queen was heard from just now."""
        self._heard_mono = self._clock.monotonic()

    def silence_s(self) -> float:
        """Return the seconds since the Queen was last heard from, never negative."""
        return max(0.0, self._clock.monotonic() - self._heard_mono)

    def is_tripped(self, limit_s: float) -> bool:
        """Return whether the Queen has been silent longer than ``limit_s``.

        Args:
            limit_s: The enrolled dead-man limit, ``offline_limit_s``.
        """
        return self.silence_s() > limit_s

    def heard_at(self) -> datetime:
        """Return the wall-clock moment the Queen was last heard, for persisting."""
        return self._clock.now() - timedelta(seconds=self.silence_s())
