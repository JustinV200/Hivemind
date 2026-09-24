"""Define PulseAudioBuzz and PulseServer: the Buzz over a lease's own PulseAudio server.

Buzz is the Exoskeleton's audio peripheral (`hivemind.exoskeleton.buzz.base`). This backend talks
to the PulseAudio server attach started for the lease (ADR-0031: its runtime and home live in
scratch, so the operator's own sound setup is never touched), described by a `PulseServer`: the
server's socket and its two null sinks. Applications on the Cell play into the *speaker* sink, and
`listen` records that sink's monitor with `parec`, stopped on schedule by coreutils' `timeout`
(which exits 124 when it did the stopping, so 124 counts as success here). `say` plays a WAV clip
from scratch into the *microphone* sink with `paplay`; the sink's monitor is the server's default
source, so any application recording from the default microphone hears the clip. The clip is read
once first, to refuse anything that is not a PCM WAV or runs past MAX_SAY_S before a sound plays,
and to bound playback by the clip's own length.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`. Built
    by `hivemind.exoskeleton.attach`; called by the `listen` tool and by the Capping gate's GUI
    surface applying a SAY step. Calls into `hivemind.cell` (CellSession),
    `hivemind.exoskeleton.commands`, `.buzz.base` and `.buzz.clips` only.

Key invariants:
    - Recordings are 16 kHz mono signed 16-bit PCM, which every speech recogniser accepts.
    - `say` plays only a clip `buzz.clips.load_clip` accepted: inside scratch, a PCM WAV, short.
    - Every command runs with a timeout of its own audio length plus a fixed margin.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the per-lease server.
    - docs/adr/0033-transcription-provider-whisper-first.md for what happens to a Recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import CellSession
from hivemind.exoskeleton.buzz.base import MAX_LISTEN_S, MIN_LISTEN_S, Recording
from hivemind.exoskeleton.buzz.clips import PERIPHERAL, load_clip
from hivemind.exoskeleton.commands import PeripheralCommand, run_peripheral

LISTEN_RATE = 16_000  # Hz; speech recognisers want 16 kHz, and the server resamples for free.
LISTEN_CHANNELS = 1  # One mixed channel of whatever the Cell plays.
AUDIO_MARGIN_S = 10.0  # Connecting, flushing and exiting, on top of the audio's own length.
_TIMEOUT_STOPPED = 124  # coreutils `timeout` exits 124 when it stopped the command on schedule.

__all__ = ["AUDIO_MARGIN_S", "LISTEN_CHANNELS", "LISTEN_RATE", "PulseAudioBuzz", "PulseServer"]


@dataclass(frozen=True, slots=True)
class PulseServer:
    """One lease's PulseAudio server: where its socket is, and its speaker and microphone sinks."""

    socket: Path  # The server's native-protocol socket, in the lease's scratch.
    cookie: Path  # Where clients keep their auth cookie, in scratch, never in a real HOME.
    speaker: str  # The null sink applications play into; `listen` records its monitor.
    microphone: str  # The null sink `say` plays into; its monitor is the default source.

    def environment(self) -> dict[str, str]:
        """Return the environment a PulseAudio client on the Cell needs to reach this server.

        Returns:
            `PULSE_SERVER` naming the socket, so no client ever finds another server, and
            `PULSE_COOKIE`, because a client with no cookie creates one under its HOME.
        """
        return {"PULSE_SERVER": f"unix:{self.socket}", "PULSE_COOKIE": str(self.cookie)}


class PulseAudioBuzz:
    """Hear and speak through one lease's PulseAudio server, through the Cell's session."""

    def __init__(self, session: CellSession, server: PulseServer) -> None:
        """Build the buzz for `server`.

        Args:
            session: The Cell's session; every command runs through it.
            server: The lease's sound server and its two sinks.
        """
        self._session = session
        self._server = server

    async def listen(self, seconds: float) -> Recording:
        """Record the speaker's monitor for `seconds`; see Buzz."""
        if not MIN_LISTEN_S <= seconds <= MAX_LISTEN_S:
            raise ValueError(f"listen records {MIN_LISTEN_S}..{MAX_LISTEN_S}s, got {seconds}.")
        # timeout sends SIGINT, which parec treats as "stop and flush", so the tail is kept.
        argv: tuple[str, ...] = ("timeout", "--signal=INT", f"{seconds:.3f}", "parec")
        argv += (f"--device={self._server.speaker}.monitor", "--raw", "--format=s16le")
        argv += (f"--rate={LISTEN_RATE}", f"--channels={LISTEN_CHANNELS}", "--latency-msec=50")
        command = PeripheralCommand(
            argv=argv,
            timeout_s=seconds + AUDIO_MARGIN_S,
            env=self._server.environment(),
            ok_codes=(0, _TIMEOUT_STOPPED),
        )
        pcm = await run_peripheral(self._session, command, PERIPHERAL, "listen")
        return Recording.from_pcm(pcm, LISTEN_RATE, LISTEN_CHANNELS)

    async def say(self, clip: Path) -> None:
        """Play the WAV at `clip` into the microphone sink; see Buzz."""
        loaded = await load_clip(self._session, clip)
        command = PeripheralCommand(
            argv=("paplay", f"--device={self._server.microphone}", str(loaded.path)),
            timeout_s=loaded.duration_s + AUDIO_MARGIN_S,
            env=self._server.environment(),
        )
        await run_peripheral(self._session, command, PERIPHERAL, "say")
