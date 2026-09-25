"""Hear one clip at the door: every check, the transcription, the scan, then where the words go.

Codingrules 8.15, "Voice is transcribed at the door": a clip from an enrolled device (a whole clip
on ``POST /v1/chat/audio``, or a push-to-talk hold gathered on the chat socket) is heard here, on
one path whichever way it came. Everything that can refuse it without a model runs first: the
intent's own capability and, for an answer, a question actually waiting on the human; the
operator's ``max_clip_seconds``, read from the WAV header or the declared length; a goal's spend
against its device's day (an interactive device over it steps up before anything is heard); and
the device's budget of audio seconds, charged the clip's whole length. Only then does the
``TRANSCRIBER`` slot run, through the Hive's provider registry and its Fanner (the seat meter every
model call passes through), so each clip is exactly one ``llm.call`` on the trail, carrying seconds
and never the audio. The transcript is outside text from a person, so the untrusted-content
scanner (roadmap step 10.6b) scores it as it does every Landing Board message, recording
``guard.injection_suspected`` with a keyed hash and never the words; a flagged goal is always held
for the human's yes. Then the words go where typed words go (``deliver``), and the clip is either
dropped, which is the default and leaves nothing holding its bytes, or kept as C2 Nectar with its
retention window (``keep_audio``). Nothing here logs or records the audio or the words.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Called by the
    voice route and the push-to-talk reader. Calls into the gate (capabilities, the rate limiter),
    the transcriber, the scanner, the Nectar seam and ``hivemind.entrance.voice.deliver``.

Key invariants:
    - No model runs for a clip that fails any check: capability, waiting question, length cap,
      step-up of an interactive device, or audio budget.
    - Exactly one transcription per clip; a failed one is a refusal, never a retry here.
    - The audio outlives this call only when ``keep_audio`` is set, and only in the Nectar seam.

See Also:
    - .claude/codingrules.md section 8.15 for the rule this implements.
    - hivemind.entrance.voice.deliver for where the words go.
    - hivemind.llm.fanner.transcription for the metering and its llm.call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from hivemind.brood_chamber import QuestionNotFoundError
from hivemind.cell import CellIdentity, CombShieldLevel
from hivemind.common.logging import get_logger
from hivemind.entrance.gate.admit import Caller, authorise
from hivemind.entrance.gate.services import EntranceServices
from hivemind.entrance.voice.deliver import (
    Delivered,
    SpokenGoal,
    deliver_answer,
    deliver_chat,
    deliver_goal,
    weigh_goal,
    words_of,
)
from hivemind.entrance.voice.errors import (
    AudioOverBudgetError,
    ClipRefusedError,
    TranscriptionFailedError,
    VoiceNotServedError,
)
from hivemind.entrance.voice.intent import ANSWER, VoiceIntent, VoiceIntentKind
from hivemind.entrance.voice.models import VoiceAccepted
from hivemind.entrance.voice.nectar import KeptAudio
from hivemind.entrance.voice.services import VoiceServices
from hivemind.guard.scanner import ScanAction, ScanRecorder, ScanSite, ScanSource
from hivemind.llm.errors import LLMError
from hivemind.llm.transcription import AudioClip, ClipProblem, Transcript, normalise_language
from hivemind.queen.intake import new_goal_request_id

# A two-minute clip on a CPU Whisper takes about as long as it lasts; this also covers the wait for
# the transcriber's seat. Past it the device is told to try again, and nothing is kept.
TRANSCRIBE_TIMEOUT_S = 180.0
# The operator's own words from an enrolled device are read at the baseline tier's thresholds,
# exactly as the Queen reads her chat (no Cell's tier applies to the human).
VOICE_TIER = CombShieldLevel.MEADOW

log = get_logger(__name__)

__all__ = ["TRANSCRIBE_TIMEOUT_S", "VOICE_TIER", "Utterance", "hear", "voice_of"]


@dataclass(frozen=True, slots=True)
class Utterance:
    """One thing a person said at the door: the clip, what they meant, and a language hint.

    Attributes:
        clip: The recording, validated (its length read from a WAV header or declared).
        intent: What the words are for.
        language: A language hint (``en``, ``en-US``); None lets the transcriber detect it.
    """

    clip: AudioClip
    intent: VoiceIntent
    language: str | None = None


def voice_of(services: EntranceServices) -> VoiceServices:
    """Return the Entrance's voice, or refuse: voice is off on this Hive.

    Args:
        services: The Entrance's services.

    Returns:
        The voice services.

    Raises:
        VoiceNotServedError: ``[entrance.voice]`` is off (or no transcriber was bound).
    """
    if services.voice is None:
        raise VoiceNotServedError()
    return services.voice


async def hear(services: EntranceServices, caller: Caller, utterance: Utterance) -> VoiceAccepted:
    """Hear one clip from an admitted device and deliver its words as its intent says.

    Args:
        services: The Entrance's services.
        caller: The admitted device (its session, capabilities and standing checked).
        utterance: The clip, its intent and a language hint.

    Returns:
        The transcript and what the words became (C2: for the speaking device alone).

    Raises:
        VoiceNotServedError: Voice is off.
        CapabilityDeniedError: An answer from a device without ``entrance:answer``.
        QuestionNotFoundError: An answer to a question not waiting on the human.
        ClipRefusedError: The clip is longer than ``max_clip_seconds``.
        StepUpRequiredError: A goal needing a step-up (an interactive device's, before anything
            is heard; a program's, held for a person once heard).
        AudioOverBudgetError: The device has sent its audio budget for now.
        TranscriptionFailedError: The transcriber failed or ran out of time.
        NothingHeardError: The clip held no words.
        TranscriptTooLongError: More words than the intent's outcome holds.
    """
    voice, intent, clip = voice_of(services), utterance.intent, utterance.clip
    await _admit(services, caller, intent)
    _within_cap(voice, clip)
    spend = await weigh_goal(services, caller) if intent.kind is VoiceIntentKind.GOAL else None
    # Charged whole, before the model runs: a clip the budget cannot pay for is never heard.
    if not services.guards.limiter.allow_audio(caller.device.id, clip.duration_s):
        raise AudioOverBudgetError()
    transcript = await _transcribe(voice, utterance)
    words = words_of(transcript.text, intent.kind)
    ref = _scan_ref(services, intent)
    flagged = await _flagged(services, voice, words, ref)
    if spend is not None and ref is not None:
        goal = SpokenGoal(ref, words, spend, confirm=voice.rules.confirm_goals or flagged)
        delivered = await deliver_goal(services, caller, goal)
    elif intent.question_id is not None:
        delivered = await deliver_answer(services, intent.question_id, words)
    else:
        delivered = await deliver_chat(services, caller, words)
    await _keep(voice, caller, clip, delivered.ref, services.clock.now())
    return _accepted(intent, words, transcript, delivered)


async def _admit(services: EntranceServices, caller: Caller, intent: VoiceIntent) -> None:
    """Refuse an answer its device may not give, or to a question not waiting on the human."""
    if intent.question_id is None:
        return  # A goal or chat needs entrance:submit, which the route or socket already checked.
    # A typed answer's own capability, at the Entrance route point: a refusal is trailed.
    await authorise(services, caller, ANSWER)
    # Latency: one local read of the Brood Chamber's pending questions.
    pending = await services.queen.human_inbox.pending_questions(services.hive.chamber)
    if all(question.id != intent.question_id for question in pending):
        raise QuestionNotFoundError(intent.question_id)


def _within_cap(voice: VoiceServices, clip: AudioClip) -> None:
    """Refuse a clip longer than ``max_clip_seconds``, measured before any model runs."""
    limit = voice.rules.max_clip_seconds
    if clip.duration_s > limit:
        detail = f"{clip.duration_s:.1f} s exceeds the {limit:g} s limit"
        raise ClipRefusedError(ClipProblem.TOO_LONG, detail)


async def _transcribe(voice: VoiceServices, utterance: Utterance) -> Transcript:
    """Transcribe the clip once on the TRANSCRIBER slot, bounded; a failure is a refusal."""
    try:
        # External await, seconds to minutes: the metered transcriber's seat queue and the
        # provider's own call; bounded here too, so a stalled server lets the device go.
        async with asyncio.timeout(TRANSCRIBE_TIMEOUT_S):
            # A device's locale (`en-US`) is reduced to the code a transcriber takes (`en`).
            language = normalise_language(utterance.language)
            return await voice.ears.hear(utterance.clip, language)
    except (LLMError, TimeoutError) as exc:
        # The failure's class only: an adapter's message may name the server, never the audio.
        log.warning("entrance.voice_transcription_failed", error=type(exc).__name__)
        raise TranscriptionFailedError() from exc


def _scan_ref(services: EntranceServices, intent: VoiceIntent) -> str | None:
    """Mint a goal's request id (its words are scanned under it), or name the question answered."""
    if intent.kind is VoiceIntentKind.GOAL:
        return new_goal_request_id(services.clock)
    return intent.question_id


async def _flagged(
    services: EntranceServices, voice: VoiceServices, words: str, ref: str | None
) -> bool:
    """Scan the words as a Landing Board message; True when the scanner flagged them."""
    records = services.enrolment.records
    stamp = records.identity
    identity = CellIdentity(hive_id=stamp.hive_id, node_id=stamp.node_id, actor=stamp.actor)
    site = ScanSite(
        source=ScanSource.LANDING_BOARD,
        consumer=stamp.hive_id,
        recorder=ScanRecorder(trail=records.trail, identity=identity, clock=services.clock),
        tier=VOICE_TIER,
        ref=ref,
    )
    # A local, bounded scan; on a flag, one trail write (the keyed hash, never the words).
    verdict = await voice.scanner.scan(words, site)
    return verdict.action is not ScanAction.PASS


async def _keep(
    voice: VoiceServices, caller: Caller, clip: AudioClip, ref: str, now: datetime
) -> None:
    """Keep the clip as C2 Nectar when ``keep_audio`` says so; otherwise nothing keeps it."""
    rules = voice.rules
    if not rules.keep_audio:
        return  # Discarded: once this call returns, nothing holds the clip's bytes.
    kept = KeptAudio(
        ref=ref,
        device_id=caller.device.id,
        media_type=clip.media_type,
        duration_s=clip.duration_s,
        data=clip.data,
        kept_at=now,
        expires_at=now + rules.retention,
    )
    await voice.nectar.deposit(kept)


def _accepted(
    intent: VoiceIntent, words: str, transcript: Transcript, delivered: Delivered
) -> VoiceAccepted:
    """Answer the device that spoke: its words, and what they became."""
    return VoiceAccepted(
        intent=intent.kind,
        transcript=words,
        language=transcript.language,
        duration_s=transcript.duration_s,
        goal=delivered.goal,
        answered=delivered.answered,
        chat=delivered.chat,
    )
