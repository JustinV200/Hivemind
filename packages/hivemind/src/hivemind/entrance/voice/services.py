"""Define what hearing a clip needs: the transcriber, the scanner, the Nectar seam and the rules.

The voice door (roadmap step 10.5f) is handed one ``VoiceServices`` by the composition root, as
the rest of the Hive Entrance (the Hive's one door) is handed ``EntranceServices``: the
``TRANSCRIBER`` slot bound through the Hive's provider registry and metered by its Fanner (the seat
meter every model call passes through), so each clip is one ``llm.call`` on the trail; the Queen's
own untrusted-content scanner, so a transcript is scored with the same patterns and keyed hash as
every other Landing Board message; the Nectar seam a kept clip is deposited through; and
``VoiceRules``, the ``[entrance.voice]`` settings the door decides with. The Entrance holds None in
their place while voice is off, which leaves the voice route unmounted.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Built by
    ``hive serve``'s composition root (``hivemind.cli.compose.entrance``) and by tests; read by the
    voice door. Calls into the transcription boundary, the scanner, the manifest's voice section
    and this package's Nectar seam.

Key invariants:
    - ``max_clip_seconds`` is the manifest's, never above the transcription boundary's own ceiling
      (a clip past that is refused while it is being built, whatever the manifest says).

See Also:
    - hivemind.llm.fanner.transcription for bind_transcriber, which builds the transcriber.
    - hivemind.entrance.voice.door for how each is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from hivemind.entrance.voice.nectar import AudioNectar
from hivemind.guard.scanner import ContentScanner
from hivemind.llm.transcription import TranscriptionProvider
from hivemind.manifest.schema.entrance import EntranceVoiceSection

__all__ = ["VoiceRules", "VoiceServices"]


@dataclass(frozen=True, slots=True)
class VoiceRules:
    """The ``[entrance.voice]`` settings the voice door decides with.

    Attributes:
        confirm_goals: Echo a spoken goal back and hold it for the human's yes.
        keep_audio: Keep a clip as C2 Nectar after transcription instead of discarding it.
        max_clip_seconds: The longest clip accepted, in seconds.
        retention: How long a kept clip stays before the sweep deletes it.
    """

    confirm_goals: bool
    keep_audio: bool
    max_clip_seconds: float
    retention: timedelta

    @classmethod
    def from_section(cls, section: EntranceVoiceSection) -> VoiceRules:
        """Read the rules from ``[entrance.voice]``.

        Args:
            section: The manifest's voice section.

        Returns:
            The rules.
        """
        return cls(
            confirm_goals=section.confirm_goals,
            keep_audio=section.keep_audio,
            max_clip_seconds=section.max_clip_seconds,
            retention=timedelta(hours=section.keep_audio_hours),
        )


@dataclass(frozen=True, slots=True)
class VoiceServices:
    """Everything the voice door needs besides the Entrance's own services.

    Attributes:
        transcriber: The TRANSCRIBER slot, bound and metered (``bind_transcriber``).
        scanner: The Queen's untrusted-content scanner, which scores every transcript.
        nectar: Where a kept clip goes (``keep_audio``); in memory until phase 7.
        rules: The ``[entrance.voice]`` settings.
    """

    transcriber: TranscriptionProvider
    scanner: ContentScanner
    nectar: AudioNectar
    rules: VoiceRules
