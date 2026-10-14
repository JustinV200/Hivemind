"""Remember the frames a node sent lately, so a reconnection can send them again: RecentFrames.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) is at-most-once
per connection, and a ``send`` that returns has only put a frame on the connection, not in the
receiver's hands. A cable pulled out from under a device leaves its connection looking alive until
the WebSocket's keepalive gives up, ``PING_INTERVAL_S + PING_TIMEOUT_S`` after the last traffic,
and every frame sent in that stretch returns as sent and is lost; the outbox (``waggle.outbox``)
cannot help, because the sender had no failure to queue it on. So a node that must get a frame
through keeps a short memory of what it sent, here, and on a reconnection sends the stretch before
it noticed again, under the same envelope ids, ahead of its outbox. The receiver's replay window
(``waggle.transport.replay``) drops what it already had, so the re-send is idempotent; that is the
reason this ring may be generous. It is a pure, bounded structure: it never reads a clock (the
caller passes ``at``, a reading of the injected Clock's ``monotonic``) and never does I/O.

Three bounds keep it honest. Memory is bounded by a count and a byte total, oldest first, whatever
the node sends. A frame is kept only ``window_s`` before the newest one, because an older frame was
sent while the link was certainly alive. And ``due`` offers a frame again only while the receiver's
replay window still remembers it, ``horizon_s`` after it was last sent, because a frame she had
already handled but forgot would be handled twice; beyond that the ring lets it go.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Written by ``waggle.transport.recent.remembering`` after
    each send that returned, read by it when a connection is made again; calls into waggle.codec
    (to measure a frame), waggle.transport.replay and waggle.transport.websocket (for the
    constants its defaults derive from) only.

Key invariants:
    - Entries are in the order they were last sent, oldest first: remembering an id that is held
      again moves it to the young end, so a re-send leaves the order it found.
    - At most ``max_frames`` entries and ``max_bytes`` of frame text are kept; the oldest go
      first, and a single frame larger than ``max_bytes`` is not kept at all.
    - ``due`` offers no frame sent more than ``window_s`` before the link was lost, and none whose
      last send is older than ``horizon_s``; it removes nothing, so a re-send that fails part way
      is made again from the start.
    - ``clear`` forgets everything: the peer said goodbye with a close frame, so nothing it was
      sent can have been swallowed.
    - Nothing here reads a clock, sleeps or touches the network.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the decision.
    - docs/waggle/spec.md section 10 and docs/waggle/gateway-contract.md section 14.
    - waggle.transport.replay for the receiver's half: the window that drops what is sent again.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import CodecError
from waggle.transport.replay import REPLAY_WINDOW_S
from waggle.transport.websocket import PING_INTERVAL_S, PING_TIMEOUT_S

# A link whose far end vanished is declared dead by the keepalive PING_INTERVAL_S + PING_TIMEOUT_S
# after the last traffic, and the node notices on its next beat after that, so what it sent in
# twice that long before it noticed may have gone into a connection already dead.
UNNOTICED_S = 2.0 * (PING_INTERVAL_S + PING_TIMEOUT_S)
# How much younger than the receiver's replay window a frame must be to be sent again: a frame she
# handled is remembered for REPLAY_WINDOW_S from its last sighting, and a minute covers the time
# the re-send itself takes to arrive and be checked.
RESEND_MARGIN_S = 60.0
RESEND_HORIZON_S = REPLAY_WINDOW_S - RESEND_MARGIN_S
MAX_RECENT_FRAMES = 256  # Minutes of the busiest Warden's results, Alarms and Nectar.
MAX_RECENT_BYTES = 4 * 1024 * 1024  # Four full frames: a small device's memory stays bounded.

__all__ = [
    "MAX_RECENT_BYTES",
    "MAX_RECENT_FRAMES",
    "RESEND_HORIZON_S",
    "RESEND_MARGIN_S",
    "UNNOTICED_S",
    "RecentFrames",
    "RecentLimits",
]

# Measures a frame the way the wire will write it, minus its signature. One codec for the module:
# it is pure and holds no state, so every ring may share it.
_SIZER = Codec()


@dataclass(frozen=True, slots=True)
class RecentLimits:
    """How long, how many and how big a RecentFrames remembers, and how long it offers a frame.

    Attributes:
        window_s: Seconds before the newest frame (and before the link was lost) that a frame may
            still have been lost in; must be > 0. ``UNNOTICED_S`` by default.
        horizon_s: Seconds after a frame's last send up to which it is offered again, the
            receiver's replay window less a margin; must be > 0.
        max_frames: The most frames kept; must be at least 1.
        max_bytes: The most frame text kept, in bytes; must be at least 1.
    """

    window_s: float = UNNOTICED_S
    horizon_s: float = RESEND_HORIZON_S
    max_frames: int = MAX_RECENT_FRAMES
    max_bytes: int = MAX_RECENT_BYTES

    def __post_init__(self) -> None:
        """Refuse limits that could remember nothing.

        Raises:
            ValueError: A time is not positive, or a count or byte total is below 1.
        """
        # A zero bound would switch the memory off while looking configured, which is worse than
        # refusing: a node that wants none does not build a ring.
        if self.window_s <= 0 or self.horizon_s <= 0:
            raise ValueError("window_s and horizon_s must be positive.")
        if self.max_frames < 1 or self.max_bytes < 1:
            raise ValueError("max_frames and max_bytes must be at least 1.")


DEFAULT_RECENT_LIMITS = RecentLimits()  # One frozen instance; RecentFrames' own default.


@dataclass(frozen=True, slots=True)
class _Entry:
    """One remembered frame: the envelope, what it weighs and when it was last sent."""

    envelope: Envelope
    size: int
    at: float


class RecentFrames:
    """The frames a node put on its connection lately, oldest first, within three bounds.

    Concurrency model: not locked. Every method is synchronous and awaits nothing, so tasks on one
    event loop never interleave inside it; do not share an instance across threads.
    """

    def __init__(self, limits: RecentLimits = DEFAULT_RECENT_LIMITS) -> None:
        """Create an empty ring with the given bounds.

        Args:
            limits: The age, count and byte bounds.
        """
        self._limits = limits
        # Envelope id -> entry, oldest send first. Insertion order is the order of the last send
        # (a re-send moves its entry to the end), which is both the order ``due`` offers frames in
        # and the order they are evicted in, so each is a walk from the front.
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._bytes = 0

    def __len__(self) -> int:
        """Return how many frames are remembered right now."""
        return len(self._entries)

    @property
    def size_bytes(self) -> int:
        """The frame text held, in bytes: what ``max_bytes`` bounds."""
        return self._bytes

    def remember(self, envelope: Envelope, at: float) -> None:
        """Note that `envelope` was just sent, at `at`, and make room for it.

        Remembering an id that is already held replaces the old entry and moves it to the young
        end: the frame has been sent again, and the order of a re-send stays the order it had.
        An envelope too large to fit the byte bound, or too large to have been sent, is not kept.

        Args:
            envelope: A frame whose ``send`` has just returned.
            at: When it was sent: the injected Clock's ``monotonic`` reading, which never goes
                backwards.
        """
        key = str(envelope.id)
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= previous.size
        size = _frame_size(envelope)
        # A frame that could not be sent, or could never be held, takes nothing from the others.
        if size is not None and size <= self._limits.max_bytes:
            self._entries[key] = _Entry(envelope, size, at)
            self._bytes += size
        self._evict(at)

    def clear(self) -> None:
        """Forget every frame: the peer closed the connection politely, so none was swallowed.

        A peer that sends its close frame has read, in order, everything sent before it; only a
        connection that dies without one can have put frames nowhere. The caller says which.
        """
        self._entries.clear()
        self._bytes = 0

    def due(self, lost_at: float, now: float) -> tuple[Envelope, ...]:
        """Return the frames to send again after a connection lost at `lost_at`, oldest first.

        Args:
            lost_at: When the node found the link dead (a reading of the Clock's ``monotonic``).
            now: The reading at the reconnection.

        Returns:
            Every frame sent no earlier than ``window_s`` before `lost_at` (older ones were sent
            while the link was alive) and no longer than ``horizon_s`` before `now` (older ones
            may be forgotten by the receiver, which would then handle them twice), in the order
            they were last sent. Nothing is removed: a frame stays held until it ages out, so a
            re-send that fails part way is made again from the start.
        """
        earliest_lost = lost_at - self._limits.window_s
        oldest_safe = now - self._limits.horizon_s
        return tuple(
            entry.envelope
            for entry in self._entries.values()
            if entry.at >= earliest_lost and entry.at > oldest_safe
        )

    def _evict(self, at: float) -> None:
        """Drop the oldest frames until the age, count and byte bounds all hold at `at`."""
        horizon = at - self._limits.window_s
        # Oldest first: the loop ends at the first frame that is young enough and fits, so a call
        # costs the number of frames it drops, not the number it keeps.
        while self._entries:
            oldest = next(iter(self._entries.values()))
            too_old = oldest.at < horizon
            too_many = len(self._entries) > self._limits.max_frames
            too_big = self._bytes > self._limits.max_bytes
            if not (too_old or too_many or too_big):
                return
            del self._entries[str(oldest.envelope.id)]
            self._bytes -= oldest.size


def _frame_size(envelope: Envelope) -> int | None:
    """Return the bytes `envelope` takes on the wire, unsigned, or None if it could not be sent."""
    try:
        return len(_SIZER.encode(envelope))
    except CodecError:
        return None  # Over the frame limit: a send of it raised, so it never reached the link.
