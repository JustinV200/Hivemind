"""Define the Exoskeleton's hearing and voice: the buzz package.

Buzz (the sound bees make) records what a Cell's applications play and plays a clip into its
microphone. `base` holds the protocol and `Recording` (WAV bytes, transient: ADR-0033), `clips` the
one check every backend applies to a clip before `say` plays it, `pulseaudio` the backend over the
lease's own PulseAudio server (run through the Cell's session), and `fake` a scripted Buzz for
tests and demos.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Built by `hivemind.exoskeleton.attach`; called by the `listen` and `say` tools. Calls into
    `hivemind.cell` and the exoskeleton's own `commands` and `errors` modules.

Key invariants:
    - A Recording's audio never reaches a log; its repr hides the bytes.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for what becomes of a Recording.

Public API:
    - Buzz, Recording, MIN_LISTEN_S, MAX_LISTEN_S, MAX_SAY_S (base): the protocol and its bounds.
    - Clip, load_clip (clips): the shared clip check.
    - PulseAudioBuzz, PulseServer, LISTEN_RATE, LISTEN_CHANNELS, AUDIO_MARGIN_S (pulseaudio).
    - FakeBuzz, FAKE_RATE (fake): the scripted Buzz.
"""

from hivemind.exoskeleton.buzz.base import MAX_LISTEN_S, MAX_SAY_S, MIN_LISTEN_S, Buzz, Recording
from hivemind.exoskeleton.buzz.clips import Clip, load_clip
from hivemind.exoskeleton.buzz.fake import FAKE_RATE, FakeBuzz
from hivemind.exoskeleton.buzz.pulseaudio import (
    AUDIO_MARGIN_S,
    LISTEN_CHANNELS,
    LISTEN_RATE,
    PulseAudioBuzz,
    PulseServer,
)

__all__ = [
    "AUDIO_MARGIN_S",
    "FAKE_RATE",
    "LISTEN_CHANNELS",
    "LISTEN_RATE",
    "MAX_LISTEN_S",
    "MAX_SAY_S",
    "MIN_LISTEN_S",
    "Buzz",
    "Clip",
    "FakeBuzz",
    "PulseAudioBuzz",
    "PulseServer",
    "Recording",
    "load_clip",
]
