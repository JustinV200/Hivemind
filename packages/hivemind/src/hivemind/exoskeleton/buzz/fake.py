"""Define FakeBuzz: a Buzz with scripted recordings, for tests and demos with no sound server.

`FakeBuzz` behaves like `PulseAudioBuzz` at every boundary a caller can see, without a sound
server: `listen` checks its duration exactly as the real one does and returns the next scripted
Recording (or silence of the requested length when nothing is scripted), and `say` checks the clip
through the same `buzz.clips.load_clip` the real backend uses (inside scratch, a PCM WAV, not too
long) and then records what it would have played. `fail_with` makes the next call fail the way a
vanished sound server does. The Buzz contract suite holds it to the real backend's behaviour.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.buzz`.
    Used by unit tests, the contract suite and the fake Exoskeleton a Worker gets in tests. Calls
    into `hivemind.cell` (CellSession), `hivemind.exoskeleton.errors` and `.buzz` only.

Key invariants:
    - Every argument the real backend refuses, this refuses with the same error type.
    - Owns mutable state (codingrules 8.5): the scripted recordings, what was said, and a pending
      failure, all changed only through its own methods.

See Also:
    - hivemind.exoskeleton.buzz.pulseaudio for the real backend this imitates.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from hivemind.cell import CellSession
from hivemind.exoskeleton.buzz.base import MAX_LISTEN_S, MIN_LISTEN_S, Recording
from hivemind.exoskeleton.buzz.clips import PERIPHERAL, load_clip
from hivemind.exoskeleton.errors import PeripheralError

FAKE_RATE = 16_000  # Hz; the rate the real backend records at, so a caller sees one format.
_SILENT_FRAME = b"\x00\x00"  # One 16-bit mono sample of silence.

__all__ = ["FAKE_RATE", "FakeBuzz"]


class FakeBuzz:
    """Hear scripted recordings and remember what was said, through the Cell's session."""

    def __init__(self, session: CellSession) -> None:
        """Build a fake Buzz over `session`, where `say` looks for its clips.

        Args:
            session: The Cell's session (a FakeSession in tests).
        """
        self._session = session
        self._recordings: deque[Recording] = deque()
        self._said: list[Path] = []
        self._failure: str | None = None

    @property
    def said(self) -> tuple[Path, ...]:
        """Every clip `say` accepted, resolved, in order."""
        return tuple(self._said)

    def script(self, *recordings: Recording) -> None:
        """Queue recordings for the next `listen` calls, one per call, in order."""
        self._recordings.extend(recordings)

    def fail_with(self, reason: str) -> None:
        """Make the next call fail as a vanished sound server would."""
        self._failure = reason

    async def listen(self, seconds: float) -> Recording:
        """Return the next scripted recording, or `seconds` of silence; see Buzz."""
        if not MIN_LISTEN_S <= seconds <= MAX_LISTEN_S:
            raise ValueError(f"listen records {MIN_LISTEN_S}..{MAX_LISTEN_S}s, got {seconds}.")
        self._raise_pending("listen")
        if self._recordings:
            return self._recordings.popleft()
        return Recording.from_pcm(_SILENT_FRAME * int(seconds * FAKE_RATE), FAKE_RATE, 1)

    async def say(self, clip: Path) -> None:
        """Check `clip` exactly as the real backend does, then remember it; see Buzz."""
        loaded = await load_clip(self._session, clip)
        self._raise_pending("say")
        self._said.append(loaded.path)

    def _raise_pending(self, operation: str) -> None:
        """Raise the failure `fail_with` queued, once."""
        if self._failure is not None:
            reason, self._failure = self._failure, None
            raise PeripheralError(PERIPHERAL, operation, reason)
