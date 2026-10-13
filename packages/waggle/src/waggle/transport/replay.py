"""Remember which envelope ids a listener has seen lately, so a replayed frame is recognised.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) leaves
deduplication to receivers (ADR-0005): a sender whose ``send`` failed after the frame reached the
peer replays it from its outbox (spec section 10), so the same Envelope (the outer wrapper every
message travels in) can arrive twice, and an attacker who captured a signed frame can send it
again. The listener's bounded half of that obligation is this window. A node is one process with
its own signing key; the window remembers the ``(node_id, id)`` pair of every envelope it was
asked about for ``window_s`` seconds and answers whether a pair was already seen, so the caller
can drop the duplicate before any receiver handles it. The window is shared by every connection a
listener accepts, which is what makes a replay on a fresh connection as visible as one on the
old. It is a pure data structure: it never reads a clock (the caller passes ``at``, read from the
injected Clock) and never does I/O, so a test drives it with a FakeClock and no timer.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.transport.limits, which a
    WebSocketServer puts in front of each accepted connection's receive path; calls into the
    standard library only.

Key invariants:
    - A pair is remembered from the moment it is first reported until ``window_s`` seconds after
      the last time it was asked about; asking again inside the window refreshes it, so a peer
      that keeps replaying one frame stays refused for as long as it keeps replaying.
    - At most ``max_ids`` pairs are kept; the oldest go first, so memory is bounded however many
      nodes or ids arrive, and a flood can only shorten the window, never grow it.
    - The same id from two nodes is two pairs: one node's ids never shadow another's.
    - ``seen`` never answers True for a pair it was not asked about before.

See Also:
    - docs/waggle/spec.md sections 3 (duplicates and freshness) and 9 (the receiver obligation).
    - docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md, which this amends.
    - waggle.transport.limits for the caller, and waggle.transport.ceiling for the other limit.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

REPLAY_WINDOW_S = 600.0  # Ten minutes: outlasts a reconnect and its outbox replay, yet stays small.
MAX_REMEMBERED_IDS = 50_000  # Tens of MB at most; one node at the ceiling fills it in 8 minutes.

__all__ = ["MAX_REMEMBERED_IDS", "REPLAY_WINDOW_S", "ReplayLimits", "ReplayWindow"]


@dataclass(frozen=True, slots=True)
class ReplayLimits:
    """How long, and how many, envelope ids a ReplayWindow keeps.

    Attributes:
        window_s: Seconds a pair is remembered after it was last asked about; must be > 0.
        max_ids: The most ``(node_id, id)`` pairs kept at once, across every node; the oldest are
            forgotten first once it is exceeded. Must be at least 1.
    """

    window_s: float = REPLAY_WINDOW_S
    max_ids: int = MAX_REMEMBERED_IDS

    def __post_init__(self) -> None:
        """Refuse a window or a cap that could not remember anything.

        Raises:
            ValueError: ``window_s`` is not positive, or ``max_ids`` is below 1.
        """
        # A zero window or cap would silently disable the check while looking configured, which
        # is worse than refusing: "no replay check" is spelled limits.replay = None.
        if self.window_s <= 0:
            raise ValueError(f"window_s must be positive, got {self.window_s}.")
        if self.max_ids < 1:
            raise ValueError(f"max_ids must be at least 1, got {self.max_ids}.")


DEFAULT_REPLAY_LIMITS = ReplayLimits()  # One frozen instance; ReplayWindow's own default.


class ReplayWindow:
    """Answer whether an envelope id was already seen, per node, inside a sliding window.

    Concurrency model: not locked. Every method is synchronous and awaits nothing, so tasks on
    one event loop never interleave inside it; do not share an instance across threads.
    """

    def __init__(self, limits: ReplayLimits = DEFAULT_REPLAY_LIMITS) -> None:
        """Create an empty window with the given bounds.

        Args:
            limits: The window length and the cap on pairs kept.
        """
        self._limits = limits
        self._window = timedelta(seconds=limits.window_s)
        # (node_id, id) -> when it was last asked about, oldest first. Insertion order is the
        # eviction and expiry order, which equals time order while `at` never goes backwards, so
        # both are a pop from the front instead of a scan.
        self._last_seen: OrderedDict[tuple[str, str], datetime] = OrderedDict()

    def __len__(self) -> int:
        """Return how many ``(node_id, id)`` pairs are remembered right now."""
        return len(self._last_seen)

    def seen(self, node_id: str, message_id: str, at: datetime) -> bool:
        """Record one sighting of an envelope id and say whether it had been seen before.

        Args:
            node_id: The envelope's ``node_id``: ids are unique per node, not globally.
            message_id: The envelope's ``id``.
            at: When the envelope was received, timezone-aware as ``Clock.now()`` returns it.

        Returns:
            True when the pair was already seen less than ``window_s`` seconds ago (a duplicate:
            the caller drops it), False when it is new or its earlier sighting has expired (the
            caller handles it). Either way the sighting is recorded.
        """
        self._expire(at)
        key = (node_id, message_id)
        # Still remembered after the expiry above, so an earlier sighting is inside the window:
        # refresh it (it moves to the young end) and report the duplicate.
        if key in self._last_seen:
            self._last_seen[key] = at
            self._last_seen.move_to_end(key)
            return True
        # First sighting: remember it, then make room if that crossed the cap.
        self._last_seen[key] = at
        if len(self._last_seen) > self._limits.max_ids:
            self._last_seen.popitem(last=False)
        return False

    def _expire(self, at: datetime) -> None:
        """Forget every pair whose last sighting is a full window or more before ``at``."""
        horizon = at - self._window
        # Oldest first: the loop ends at the first pair that is still young enough, so a call
        # costs the number of pairs it forgets, not the number it keeps.
        while self._last_seen:
            oldest_key = next(iter(self._last_seen))
            if self._last_seen[oldest_key] > horizon:
                return
            del self._last_seen[oldest_key]
