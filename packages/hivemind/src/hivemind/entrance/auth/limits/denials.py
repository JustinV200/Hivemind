"""Provide DenialCounter: lock a device whose capability denials suddenly burst.

A device that is refused by the Guard again and again, in a short time, is not behaving like the
device the operator approved: a submit-only program suddenly calling observe routes is the ADR's
example (ADR-0041). ``DenialCounter.record`` is called once per capability denial of a device;
when ``lockout_denials`` of them fall inside ``lockout_denial_window_s``, it locks the device
(reason ``denial_burst``) through the enrolment step's ``lock``, which records
``guard.entrance_locked``, ends the device's sessions and tells every other device. Only a
loopback unlock reopens it.

The windows are **in memory by design**: they hold the last ``lockout_denial_window_s`` seconds of
denials and nothing older, and the lock they lead to is persisted by the device state machine; a
restart that forgets a half-full window only gives a misbehaving device one more window, while
persisting every denial would put a write on the hot path of every refusal. Memory is bounded:
each device's window holds at most ``lockout_denials`` moments, and past ``capacity`` devices the
least recently denied is forgotten.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.limits``. Called
    by the Landing Board's capability check (the guard's Entrance route enforcement point) for
    every denial it hands a device (later steps). Calls into ``hivemind.entrance.enrol.standing``
    (``lock``) and waggle's clock.

Key invariants:
    - A lock goes through ``hivemind.entrance.enrol.standing.lock``, never a status write here.
    - A window is cleared when it locks its device, so a later unlock starts from nothing.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Lockout, rate
      limits, travel lock".
    - hivemind.entrance.enrol.standing for the lock itself.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from datetime import timedelta

from hivemind.common.logging import get_logger
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.standing import LockReason, lock
from hivemind.entrance.errors import DeviceNotFoundError, DeviceStatusConflictError
from hivemind.manifest import EntranceSection
from waggle.ids import DeviceId

MAX_TRACKED_DEVICES = 1_024  # Denial windows kept at once: far more devices than a Hive has.
LOCKING_ACTOR = "system"  # A denial burst is locked by the Entrance itself, not a person.

log = get_logger(__name__)

__all__ = ["LOCKING_ACTOR", "MAX_TRACKED_DEVICES", "DenialCounter"]


class DenialCounter:
    """Sliding windows of capability denials per device; a burst locks the device."""

    def __init__(
        self,
        deps: EnrolmentDeps,
        threshold: int,
        window: timedelta,
        capacity: int = MAX_TRACKED_DEVICES,
    ) -> None:
        """Build a counter with no windows yet.

        Args:
            deps: The enrolment dependencies ``lock`` needs; its clock's ``monotonic`` reading
                times the windows.
            threshold: Denials inside ``window`` that lock a device (``lockout_denials``); >= 1.
            window: How far back a denial still counts (``lockout_denial_window_s``); > 0.
            capacity: Devices whose windows are kept at once; >= 1.

        Raises:
            ValueError: The threshold or capacity is below 1, or the window is not positive.
        """
        if threshold < 1 or capacity < 1 or window <= timedelta(0):
            raise ValueError("A denial counter needs a threshold, a capacity and a window.")
        self._deps = deps
        self._threshold = threshold
        self._window_s = window.total_seconds()
        self._capacity = capacity
        # Per device, the monotonic moments of its recent denials; insertion order is recency.
        self._windows: OrderedDict[DeviceId, deque[float]] = OrderedDict()

    @classmethod
    def from_section(cls, deps: EnrolmentDeps, section: EntranceSection) -> DenialCounter:
        """Build a counter from ``[entrance] lockout_denials`` and ``lockout_denial_window_s``.

        Args:
            deps: The enrolment dependencies.
            section: The manifest's ``[entrance]`` section.

        Returns:
            The counter.
        """
        window = timedelta(seconds=section.lockout_denial_window_s)
        return cls(deps, section.lockout_denials, window)

    async def record(self, device_id: DeviceId) -> bool:
        """Count one capability denial of ``device_id``; lock it when the window fills.

        Args:
            device_id: The authenticated device the Guard just refused.

        Returns:
            True when this denial locked the device; False otherwise (including when it was
            already locked, or is no longer approved).
        """
        if not self._fill(device_id):
            return False
        try:
            # Latency: one local transaction (status and event), then offboarding and a notice.
            await lock(self._deps, device_id, LOCKING_ACTOR, LockReason.DENIAL_BURST)
        except (DeviceStatusConflictError, DeviceNotFoundError):
            # Already locked, revoked or expired by something else: that decision stands.
            log.debug("entrance.denial_lock_superseded", device_id=device_id)
            return False
        log.info("entrance.denial_burst_locked", device_id=device_id, denials=self._threshold)
        return True

    def _fill(self, device_id: DeviceId) -> bool:
        """Add one denial to the device's window; return True (and clear it) when it is full."""
        now = self._deps.records.clock.monotonic()
        window = self._windows.pop(device_id, None) or deque(maxlen=self._threshold)
        # Drop what fell out of the window, oldest first.
        while window and now - window[0] >= self._window_s:
            window.popleft()
        window.append(now)
        if len(window) >= self._threshold:
            # Cleared: the lock ends this burst, and an unlock starts from an empty window.
            return True
        self._windows[device_id] = window
        while len(self._windows) > self._capacity:
            self._windows.popitem(last=False)
        return False
