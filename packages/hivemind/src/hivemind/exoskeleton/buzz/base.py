"""Define Buzz and Recording: how a Worker hears and speaks through its Cell's audio.

Buzz (the sound bees make) is the Exoskeleton's audio peripheral. `listen` records what the Cell's
applications are playing (the monitor of the speaker the lease's sound server provides) for a
bounded number of seconds, as a `Recording`: WAV bytes, never kept past the call unless a tool
writes them to scratch on purpose (ADR-0033: audio is transient). `say` plays a WAV clip from
scratch into the Cell's microphone, so an application listening there hears it. Implementations
run their commands through a `CellSession` (codingrules section 8.7).

Latency classes used below: *bounded by the call* means the call lasts as long as the audio it
records or plays, plus a small margin; every call carries its own timeout.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`.
    Implemented by `hivemind.exoskeleton.buzz.pulseaudio.PulseAudioBuzz` and
    `hivemind.exoskeleton.buzz.fake.FakeBuzz`; called by the `listen` and `say` tools, which hand a
    Recording to the transcription provider (`hivemind.llm.transcription`) unless the bound model
    hears audio itself. Calls into pydantic and the standard library only.

Key invariants:
    - A Recording's `wav` is a complete RIFF/WAVE file whose header agrees with its fields.
    - `repr(recording)` never contains the audio bytes.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for where audio is and is not kept.
    - hivemind.exoskeleton.buzz.pulseaudio for the PulseAudio backend.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

MIN_LISTEN_S = 0.1  # Shorter than any word; also keeps a recorder's own timeout from reading 0.
MAX_LISTEN_S = 60.0  # One call records at most a minute; longer is a different task.
MAX_SAY_S = 60.0  # One clip plays for at most a minute; a longer speech is several steps.

__all__ = ["MAX_LISTEN_S", "MAX_SAY_S", "MIN_LISTEN_S", "Buzz", "Recording"]


class Recording(BaseModel):
    """A short recording from the Cell's speaker: WAV bytes and what they hold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wav: bytes = Field(repr=False, description="A complete RIFF/WAVE file; never logged.")
    sample_rate: int = Field(gt=0, description="Samples per second per channel.")
    channels: int = Field(ge=1, description="Interleaved channels.")
    duration_s: float = Field(ge=0, description="Seconds of audio the file holds.")

    @classmethod
    def from_pcm(cls, pcm: bytes, sample_rate: int, channels: int) -> Recording:
        """Wrap raw signed 16-bit little-endian PCM into a WAV Recording.

        Args:
            pcm: The samples, interleaved when `channels` > 1.
            sample_rate: Samples per second per channel.
            channels: How many channels `pcm` interleaves.

        Returns:
            The Recording, its duration computed from the sample count.
        """
        frame_bytes = 2 * channels  # 16-bit samples.
        usable = len(pcm) - len(pcm) % frame_bytes  # A torn last frame is dropped, not padded.
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(pcm[:usable])
        duration = usable / frame_bytes / sample_rate
        wav = buffer.getvalue()
        return cls(wav=wav, sample_rate=sample_rate, channels=channels, duration_s=duration)


class Buzz(Protocol):
    """Hear what the Cell plays, and speak into its microphone."""

    async def listen(self, seconds: float) -> Recording:
        """Record what the Cell's applications play, for `seconds`.

        Latency: bounded by the call (`seconds`, plus a small margin). Failure: PeripheralError
        when the sound server is gone or the recorder fails or overruns its timeout.

        Args:
            seconds: How long to record; MIN_LISTEN_S to MAX_LISTEN_S.

        Returns:
            The recording.

        Raises:
            PeripheralError: Recording failed.
            ValueError: `seconds` is out of range.
        """
        ...

    async def say(self, clip: Path) -> None:
        """Play the WAV file at `clip` into the Cell's microphone, and return when it has played.

        Latency: bounded by the call (the clip's own length, plus a small margin). Failure:
        PeripheralError when the clip is outside scratch, is not a PCM WAV, runs longer than
        MAX_SAY_S, the sound server is gone, or playback fails.

        Args:
            clip: The clip's path, relative to the session's scratch directory or inside it.

        Raises:
            PeripheralError: Playback failed.
        """
        ...
