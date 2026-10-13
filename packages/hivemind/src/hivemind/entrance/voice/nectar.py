"""Define the seam the human's media is deposited through as C2 Nectar, and its retention sweep.

A clip is discarded once it is transcribed (codingrules 8.15) unless ``[entrance.voice]
keep_audio`` is set, in which case it is kept as Nectar (the Hive's raw material before it ripens
into Honey) labelled ``C2``, since the human's voice is personal, for ``keep_audio_hours`` and no
longer. An image a device sends to ``POST /v1/nectar/images`` (roadmap step 12.12) is kept the same
way, C2 for its retention window, so it goes through the same seam: ``KeptAudio`` and ``KeptImage``
are the two things a device's media becomes. Nectar intake lands in phase 7, so this module names
the seam, ``MediaNectar``: deposit a kept item, and sweep away every item past its retention
window. ``InMemoryMediaNectar`` is the stand-in until then, the same pattern the Guard Bee's
report sink and the Capping audit's findings sink follow; phase 7's Nectar intake implements the
protocol and the composition root swaps it in. The in-memory store is bounded (a count and a byte
total, oldest evicted first), and the Hive Entrance runs its sweep on its own timer, so a kept item
never outlives its window by more than one sweep interval. A kept item never reaches a log line, a
repr or the trail: its bytes are ``repr=False`` and nothing here records them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Deposited into
    by the voice door once a clip has become a goal, an answer or a chat line, and by the image
    door (``hivemind.entrance.images``); swept by the Hive Entrance's background sweep; built by the
    composition root. Calls into ``hivemind.cell`` (HoneyClearance), the transcription boundary's
    ``AudioMediaType`` and waggle's ids only.

Key invariants:
    - Every kept item is ``C2`` and carries its own expiry: kept_at plus the retention window.
    - ``sweep`` removes every item whose expiry has passed and nothing else.
    - The in-memory store never holds more than its count and byte bounds, whichever kind it holds.

See Also:
    - hivemind.workers.roles.guard_bee.sink for GuardReportSink, the seam this one mirrors.
    - .claude/roadmap.md phase 7 for the Nectar intake that implements it for real.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from hivemind.cell import HoneyClearance
from hivemind.llm.transcription import AudioMediaType
from waggle.ids import DeviceId, NectarId, TaskId

MAX_KEPT_CLIPS = 256  # Items (clips and images) the stand-in keeps at once; the oldest goes first.
MAX_KEPT_BYTES = 128 * 1024 * 1024  # 128 MiB: about five thousand typical Opus clips.

__all__ = [
    "MAX_KEPT_BYTES",
    "MAX_KEPT_CLIPS",
    "InMemoryMediaNectar",
    "KeptAudio",
    "KeptImage",
    "KeptMedia",
    "MediaNectar",
]


@dataclass(frozen=True, slots=True)
class KeptAudio:
    """One clip kept after transcription, on its way into Nectar.

    Attributes:
        ref: What the clip became: the goal request, the question or the chat line it gave.
        device_id: The device that spoke it.
        media_type: Its format.
        duration_s: Its length in seconds.
        data: The encoded audio; C2, never in a repr.
        kept_at: When it was kept.
        expires_at: When the retention sweep deletes it.
        clearance: Always C2: the human's own voice.
    """

    ref: str
    device_id: DeviceId
    media_type: AudioMediaType
    duration_s: float
    data: bytes = field(repr=False)
    kept_at: datetime
    expires_at: datetime
    clearance: HoneyClearance = HoneyClearance.C2

    def __post_init__(self) -> None:
        """Refuse a clip labelled below C2, or one that expires before it was kept."""
        if self.clearance is not HoneyClearance.C2:
            raise ValueError("A kept clip is the human's own voice: always C2.")
        if self.expires_at <= self.kept_at:
            raise ValueError("A kept clip expires after it is kept.")


@dataclass(frozen=True, slots=True)
class KeptImage:
    """One image a device sent, kept as C2 Nectar with where it came from.

    Attributes:
        ref: The Nectar id the image was given; what the device is answered with.
        device_id: The device that sent it.
        media_type: Its format (a PNG, JPEG or WebP media type), as the device labelled it.
        data: The encoded image; C2, never in a repr.
        caption: The words the device sent with it, or None; C2 as well.
        task_id: The task the device said it concerns, or None.
        kept_at: When it was kept.
        expires_at: When the retention sweep deletes it.
        clearance: Always C2: it comes from the human.
    """

    ref: NectarId
    device_id: DeviceId
    media_type: str
    data: bytes = field(repr=False)
    caption: str | None = field(repr=False)
    task_id: TaskId | None
    kept_at: datetime
    expires_at: datetime
    clearance: HoneyClearance = HoneyClearance.C2

    def __post_init__(self) -> None:
        """Refuse an image labelled below C2, or one that expires before it was kept."""
        if self.clearance is not HoneyClearance.C2:
            raise ValueError("A kept image comes from the human: always C2.")
        if self.expires_at <= self.kept_at:
            raise ValueError("A kept image expires after it is kept.")

    @property
    def size(self) -> int:
        """The image's size in bytes."""
        return len(self.data)


KeptMedia = KeptAudio | KeptImage  # What a device's media becomes: a clip or an image.


class MediaNectar(Protocol):
    """Deposit kept clips and images as C2 Nectar and sweep them after their retention window."""

    async def deposit(self, kept: KeptMedia) -> None:
        """Keep one clip or image until its expiry.

        Args:
            kept: The item, C2, with its expiry.
        """
        ...

    async def sweep(self, now: datetime) -> int:
        """Delete every kept item whose expiry is at or before ``now``.

        Args:
            now: The sweep's moment, on the Hive's clock.

        Returns:
            How many items were deleted.
        """
        ...


class InMemoryMediaNectar:
    """A bounded, in-memory MediaNectar: the stand-in until phase 7's Nectar intake.

    Owns its store: deposits and sweeps never await, so each is atomic on the event loop. Clips and
    images share its bounds: a ``max_clips`` count of items and a ``max_bytes`` total of bytes.
    """

    def __init__(self, max_clips: int = MAX_KEPT_CLIPS, max_bytes: int = MAX_KEPT_BYTES) -> None:
        """Build an empty store.

        Args:
            max_clips: The most items (clips and images) kept at once; >= 1.
            max_bytes: The most bytes kept at once; >= 1.

        Raises:
            ValueError: A bound is below 1.
        """
        if min(max_clips, max_bytes) < 1:
            raise ValueError("The kept-audio bounds must be at least 1.")
        self._max_clips = max_clips
        self._max_bytes = max_bytes
        # Insertion order is age order, so eviction past a bound takes the oldest first.
        self._kept: OrderedDict[int, KeptMedia] = OrderedDict()
        self._bytes = 0
        self._next = 0

    @property
    def kept(self) -> tuple[KeptMedia, ...]:
        """Every clip and image kept now, oldest first."""
        return tuple(self._kept.values())

    async def deposit(self, kept: KeptMedia) -> None:
        """Keep ``kept``, evicting the oldest items while a bound is exceeded.

        Args:
            kept: The clip or image, C2, with its expiry.
        """
        self._kept[self._next] = kept
        self._next += 1
        self._bytes += len(kept.data)
        # Bounded memory: the oldest go first, the newest (this one) always stays.
        while len(self._kept) > 1 and (
            len(self._kept) > self._max_clips or self._bytes > self._max_bytes
        ):
            _, evicted = self._kept.popitem(last=False)
            self._bytes -= len(evicted.data)

    async def sweep(self, now: datetime) -> int:
        """Delete every item past its retention window; see ``MediaNectar.sweep``."""
        expired = [key for key, kept in self._kept.items() if kept.expires_at <= now]
        # Each expired item leaves the store, its bytes with it.
        for key in expired:
            self._bytes -= len(self._kept.pop(key).data)
        return len(expired)
