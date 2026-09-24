"""Implement the Exoskeleton's audio tools: listen to what the Cell plays, and say a clip.

Buzz (the sound bees make: the Exoskeleton's audio peripheral) records what this task's
applications play and plays a clip into its microphone. `listen` is read-only: it records for a
bounded number of seconds and hands the model what was heard, which (roadmap step 6.5) is the
recording itself, as a `hivemind.llm.AudioPart` in the result's media, when the bound model
declares `audio`, and otherwise a transcript from `ModelSlot.TRANSCRIBER` through the Worker's
`Ears` (6.5a). The recording is transient (ADR-0033): it is never written anywhere, logged, or put
in a result's text. `say` is an action: it proposes one SAY step through `act` like every other
GUI action, after checking the clip here exactly as Buzz will (inside scratch, a PCM WAV, not too
long), since a step that fails at apply time raises an Alarm. The microphone belongs to the sound
server this lease started, so speaking into it is `scratch_write` unless the call says otherwise.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Offered by
    `offer.exoskeleton_specs` when a Buzz is attached (`listen` only when the Worker can hear:
    Ears, or a model that takes audio). Calls into `hivemind.exoskeleton.buzz` (the listen bounds,
    load_clip), `hivemind.exoskeleton` (PeripheralError, Recording), `hivemind.llm` (AudioPart,
    AudioClip, Ears, LLMError), `hivemind.supervision.capping` (RiskTier), this package's `act`,
    `arguments` and `errors`, `hivemind.workers.tools.registry` and waggle only.

Key invariants:
    - `listen` never proposes and never transcribes for a model that hears audio itself.
    - No audio byte reaches a result's text, an error message or a log.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for where audio is and is not kept.
    - hivemind.llm.transcription for Ears and the transcriber chain behind it.
    - hivemind.exoskeleton.buzz for Buzz, Recording and the clip check.
"""

from __future__ import annotations

import base64
from pathlib import Path

from hivemind.cell import CellSession
from hivemind.exoskeleton import Buzz, PeripheralError, Recording
from hivemind.exoskeleton.buzz import MAX_LISTEN_S, MIN_LISTEN_S, load_clip
from hivemind.llm import AudioClip, AudioPart, Ears, JsonObject, LLMError
from hivemind.supervision.capping import RiskTier
from hivemind.workers.tools.exoskeleton.act import GuiAction, act, attached, need
from hivemind.workers.tools.exoskeleton.arguments import (
    action_definition,
    build_step,
    read_definition,
    required_text,
)
from hivemind.workers.tools.exoskeleton.errors import (
    GuiArgumentError,
    PeripheralMissingError,
    PeripheralReadError,
)
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec
from waggle.messages.capping import GuiOp

WAV_MEDIA_TYPE = "audio/wav"  # What a Recording's bytes are: a whole RIFF/WAVE file (buzz.base).
MAX_TRANSCRIPT_RESULT_CHARS = 8_000  # A minute of speech is ~1,000 characters; far past it is cut.

LISTEN_DEFINITION = read_definition(
    "listen",
    f"Record what this task's applications play for seconds ({MIN_LISTEN_S} to {MAX_LISTEN_S}) "
    "and hear it. Read-only: nothing is proposed or changed.",
    {"seconds": {"type": "number"}},
    ("seconds",),
)
SAY_DEFINITION = action_definition(
    "say",
    "Play a WAV clip from scratch into this task's microphone, so an application listening there "
    "hears it. Proposed through the Capping gate, which applies it and then checks expect.",
    {"clip": {"type": "string", "description": "The clip's path in scratch."}},
    ("clip",),
)

__all__ = [
    "LISTEN_DEFINITION",
    "LISTEN_SPEC",
    "MAX_TRANSCRIPT_RESULT_CHARS",
    "SAY_DEFINITION",
    "SAY_SPEC",
    "WAV_MEDIA_TYPE",
    "listen",
    "say",
]


async def listen(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Record for `seconds`, then hand the model the audio (it hears) or a transcript.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `seconds`, MIN_LISTEN_S to MAX_LISTEN_S.

    Returns:
        For a model that declares `audio`, a line saying how long and the recording as media;
        otherwise what the transcriber heard, as text.

    Raises:
        GuiArgumentError: `seconds` is out of range.
        PeripheralMissingError: No audio is attached, or nothing can hear (no Ears).
        PeripheralReadError: Recording or transcription failed.
    """
    ctx = invocation.ctx
    buzz = need(attached(invocation).peripherals.buzz, "audio")
    recording = await _record(buzz, _seconds(arguments))
    if ctx.bound.hears:
        # The bound model hears audio itself, and so would every fallback: no transcription call,
        # the recording goes to it (a fallback without audio would refuse the part outright).
        clip = AudioPart(
            media_type=WAV_MEDIA_TYPE, data_base64=base64.b64encode(recording.wav).decode("ascii")
        )
        return ToolOutput(text=f"Recorded {recording.duration_s:.1f}s, attached.", media=(clip,))
    return ToolOutput(text=await _transcribe(ctx.ears, recording))


async def say(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose playing the scratch clip at `clip` into the microphone.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `clip`, optional `expect` and `irreversible`.

    Returns:
        The gate's outcome, as tool output.

    Raises:
        GuiArgumentError: The clip is outside scratch, missing, not a PCM WAV, or too long.
        PeripheralMissingError: No audio is attached.
    """
    need(attached(invocation).peripherals.buzz, "audio")
    clip = required_text(arguments, "clip")
    await _check_clip(invocation.ctx.session, clip)
    step = build_step({"op": GuiOp.SAY, "clip": clip})
    # The lease's own sound server (module docstring); `irreversible` raises it in `act`.
    return await act(invocation, GuiAction("say", (step,), RiskTier.SCRATCH_WRITE, arguments))


LISTEN_SPEC = ToolSpec(definition=LISTEN_DEFINITION, run=listen)
SAY_SPEC = ToolSpec(definition=SAY_DEFINITION, run=say)


def _seconds(arguments: JsonObject) -> float:
    """Return the call's `seconds`, refused outside the range one recording may last."""
    value = arguments.get("seconds")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise GuiArgumentError("seconds must be a number.")
    if not MIN_LISTEN_S <= value <= MAX_LISTEN_S:
        raise GuiArgumentError(f"seconds must be between {MIN_LISTEN_S} and {MAX_LISTEN_S}.")
    return float(value)


async def _record(buzz: Buzz, seconds: float) -> Recording:
    """Record `seconds` of what the Cell plays, turning a failure into a readable one."""
    try:
        # External await: lasts the recording itself plus Buzz's own margin, bounded by its
        # timeout; an overrun arrives as a PeripheralError.
        return await buzz.listen(seconds)
    except PeripheralError as error:
        raise PeripheralReadError("listen", error.reason) from error


async def _transcribe(ears: Ears | None, recording: Recording) -> str:
    """Hear `recording` through the transcriber, and say what was heard in one bounded text."""
    if ears is None:
        raise PeripheralMissingError("transcriber")
    try:
        clip = AudioClip.from_wav(recording.wav)
    except ValueError:
        # WHY: the cause is dropped, not chained: a validation error keeps its input, the audio.
        raise PeripheralReadError("listen", "the recording is not a readable WAV") from None
    try:
        # External await: a transcription on the transcriber slot, seconds for a short clip,
        # bounded by the provider's own timeout; an outage moves along the chain inside Ears.
        transcript = await ears.hear(clip)
    except LLMError as error:
        raise PeripheralReadError("listen", f"the transcriber failed: {error}") from error
    heard = transcript.text.strip()
    if not heard:
        return f"Heard {transcript.duration_s:.1f}s of audio with no speech in it."
    if len(heard) > MAX_TRANSCRIPT_RESULT_CHARS:
        heard = f"{heard[:MAX_TRANSCRIPT_RESULT_CHARS]}...[truncated, {len(heard)} chars total]"
    language = f" ({transcript.language})" if transcript.language else ""
    return f"Heard {transcript.duration_s:.1f}s of audio{language}. Transcript: {heard}"


async def _check_clip(session: CellSession, clip: str) -> None:
    """Check the clip as Buzz will before it plays, so a typo is a refusal, not an Alarm."""
    try:
        await load_clip(session, Path(clip))
    except PeripheralError as error:
        raise GuiArgumentError(f"clip: {error.reason}") from error
