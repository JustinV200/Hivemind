"""Define the seam a kept voice clip is deposited through as C2 Nectar, and its retention sweep.

A clip is discarded once it is transcribed (codingrules 8.15) unless ``[entrance.voice]
keep_audio`` is set, in which case it is kept as Nectar (the Hive's raw material before it ripens
into Honey) labelled ``C2``, since the human's voice is personal, for ``keep_audio_hours`` and no
longer. Nectar intake lands in phase 7, so this module names the seam, ``AudioNectar``: deposit a
kept clip, and sweep away every clip past its retention window. ``InMemoryAudioNectar`` is the
stand-in until then, the same pattern the Guard Bee's report sink and the Capping audit's findings
sink follow; phase 7's Nectar intake implements the protocol and the composition root swaps it in.
The in-memory store is bounded (a count and a byte total, oldest evicted first), and the Hive
Entrance runs its sweep on its own timer, so a kept clip never outlives its window by more than one
sweep interval. A kept clip never reaches a log line, a repr or the trail: its bytes are
``repr=False`` and nothing here records them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Deposited into
    by the voice door once a clip has become a goal, an answer or a chat line; swept by the Hive
    Entrance's background sweep; built by the composition root. Calls into ``hivemind.cell``
    (HoneyClearance) and the transcription boundary's ``AudioMediaType`` only.

Key invariants:
    - Every kept clip is ``C2`` and carries its own expiry: kept_at plus the retention window.
    - ``sweep`` removes every clip whose expiry has passed and nothing else.
    - The in-memory store never holds more than its count and byte bounds.

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
from waggle.ids import DeviceId

MAX_KEPT_CLIPS = 256  # Clips the in-memory stand-in keeps at once; the oldest goes first past it.
MAX_KEPT_BYTES = 128 * 1024 * 1024  # 128 MiB: about five thousand typical Opus clips.

__all__ = ["MAX_KEPT_BYTES", "MAX_KEPT_CLIPS", "AudioNectar", "InMemoryAudioNectar", "KeptAudio"]


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


class AudioNectar(Protocol):
    """Deposit kept clips as C2 Nectar and sweep them away after their retention window."""

    async def deposit(self, kept: KeptAudio) -> None:
        """Keep one clip until its expiry.

        Args:
            kept: The clip, C2, with its expiry.
        """
        ...

    async def sweep(self, now: datetime) -> int:
        """Delete every kept clip whose expiry is at or before ``now``.

        Args:
            now: The sweep's moment, on the Hive's clock.

        Returns:
            How many clips were deleted.
        """
        ...


class InMemoryAudioNectar:
    """A bounded, in-memory AudioNectar: the stand-in until phase 7's Nectar intake.

    Owns its store: deposits and sweeps never await, so each is atomic on the event loop.
    """

    def __init__(self, max_clips: int = MAX_KEPT_CLIPS, max_bytes: int = MAX_KEPT_BYTES) -> None:
        """Build an empty store.

        Args:
            max_clips: The most clips kept at once; >= 1.
            max_bytes: The most audio bytes kept at once; >= 1.

        Raises:
            ValueError: A bound is below 1.
        """
        if min(max_clips, max_bytes) < 1:
            raise ValueError("The kept-audio bounds must be at least 1.")
        self._max_clips = max_clips
        self._max_bytes = max_bytes
        # Insertion order is age order, so eviction past a bound takes the oldest first.
        self._kept: OrderedDict[int, KeptAudio] = OrderedDict()
        self._bytes = 0
        self._next = 0

    @property
    def kept(self) -> tuple[KeptAudio, ...]:
        """Every clip kept now, oldest first."""
        return tuple(self._kept.values())

    async def deposit(self, kept: KeptAudio) -> None:
        """Keep ``kept``, evicting the oldest clips while a bound is exceeded.

        Args:
            kept: The clip, C2, with its expiry.
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
        """Delete every clip past its retention window; see ``AudioNectar.sweep``."""
        expired = [key for key, kept in self._kept.items() if kept.expires_at <= now]
        # Each expired clip leaves the store, its bytes with it.
        for key in expired:
            self._bytes -= len(self._kept.pop(key).data)
        return len(expired)
