"""Load the WAV clip a Buzz is asked to say: inside scratch, a real PCM WAV, not too long.

`Buzz.say` plays a clip the bee put in its scratch directory into the Cell's microphone. Every
implementation must refuse the same clips for the same reasons, before any sound plays: a path that
resolves outside scratch (symlinks and `..` included), a file that is not a PCM WAV, and a clip
longer than MAX_SAY_S. The clip's own length also bounds how long playback may take. This module is
that one check, shared by `buzz.pulseaudio` and `buzz.fake`, so the contract suite holds them to
the same behaviour.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.buzz`.
    Called by `PulseAudioBuzz.say` and `FakeBuzz.say`. Calls into `hivemind.cell` (CellSession,
    resolve_scratch_path, PathNotAllowedError), `hivemind.exoskeleton.errors` and `.buzz.base`.

Key invariants:
    - A clip is read once, through the session, and only after its path is known to be in scratch.

See Also:
    - hivemind.exoskeleton.buzz.base for MAX_SAY_S and the Buzz contract.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import CellSession, PathNotAllowedError, resolve_scratch_path
from hivemind.exoskeleton.buzz.base import MAX_SAY_S
from hivemind.exoskeleton.errors import PeripheralError

PERIPHERAL = "buzz"  # How every Buzz names itself in a PeripheralError.

__all__ = ["PERIPHERAL", "Clip", "load_clip"]


@dataclass(frozen=True, slots=True)
class Clip:
    """A clip `say` may play: where it is on the Cell, and how long it runs."""

    path: Path  # Absolute, resolved, inside scratch.
    duration_s: float


async def load_clip(session: CellSession, clip: Path) -> Clip:
    """Resolve and check the clip at `clip`, or raise the PeripheralError `say` reports.

    Args:
        session: The Cell's session; the clip is read through it.
        clip: Relative to scratch, or absolute inside it.

    Returns:
        The clip's resolved path and length.

    Raises:
        PeripheralError: The path leaves scratch, the file is missing or not a PCM WAV, or it
            runs longer than MAX_SAY_S.
    """
    try:
        path = resolve_scratch_path(session.scratch_dir, clip)
    except PathNotAllowedError as error:
        raise PeripheralError(PERIPHERAL, "say", "the clip must be inside scratch") from error
    try:
        data = await session.get_file(path)
    except FileNotFoundError as error:
        raise PeripheralError(PERIPHERAL, "say", "there is no clip at that path") from error
    duration = _wav_duration(data)
    if duration > MAX_SAY_S:
        reason = f"the clip runs {duration:.1f}s, over {MAX_SAY_S}s"
        raise PeripheralError(PERIPHERAL, "say", reason)
    return Clip(path=path, duration_s=duration)


def _wav_duration(data: bytes) -> float:
    """Return a PCM WAV file's length in seconds, or raise PeripheralError when it is not one."""
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
    except (wave.Error, EOFError) as error:
        raise PeripheralError(PERIPHERAL, "say", "the clip is not a PCM WAV file") from error
    if rate <= 0:
        raise PeripheralError(PERIPHERAL, "say", "the clip's WAV header states no sample rate")
    return frames / rate
