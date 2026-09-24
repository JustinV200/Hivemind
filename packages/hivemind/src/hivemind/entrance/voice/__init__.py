"""Hear the human's voice at the Hive Entrance: clips and push-to-talk, transcribed at the door.

Roadmap step 10.5f, codingrules 8.15 ("Voice is transcribed at the door"): an enrolled device may
speak instead of typing, a whole clip on ``POST /v1/chat/audio`` (a raw body with its media type,
ADR-0032) or a push-to-talk hold on its chat socket (audio frames, then an end frame). Both reach
one door, ``hear``: the intent (``goal``, ``answer:<question id>`` or ``chat``) and every limit a
clip must pass are checked first, so a refused clip never reaches a model; the clip is transcribed
once on ``ModelSlot.TRANSCRIBER`` through the Hive's registry and Fanner, the Hive Stand's own
Whisper by default; the transcript is scanned as outside text; and the words go where the same
words typed would go, through the Queen's door. A spoken goal is echoed back and held
AWAITING_CONFIRMATION until a person confirms it (``[entrance.voice] confirm_goals``), so a
misheard sentence never spends anything; an answer and chat go straight through. Audio and
transcript are C2: the transcript goes back to the speaking device alone, neither ever reaches a
log line or the trail, and the audio is discarded once heard unless ``keep_audio`` deposits it,
C2, through the Nectar seam for its retention window.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. The route joins the
    route table (``hivemind.entrance.routes.registry``); the reader runs on the chat view
    (``hivemind.entrance.streams.views.landing``); the services are built by ``hive serve``'s
    composition root. Calls into the gate, the Entrance's intake, the Queen's door, the
    transcription boundary, the untrusted-content scanner and the manifest's voice section.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No model runs for a clip from a device that is not APPROVED, lacks the intent's capability,
      is over its audio budget, or sent a clip longer than ``max_clip_seconds``.
    - Exactly one ``llm.call`` on TRANSCRIBER per clip heard; none for a clip refused.

See Also:
    - .claude/codingrules.md section 8.15 for the rule.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the intents.
    - hivemind.llm.transcription and hivemind.llm.fanner.transcription for the transcriber.

Public API:
    - hear, Utterance, voice_of, TRANSCRIBE_TIMEOUT_S, VOICE_TIER: the one door (door).
    - VoiceIntent, VoiceIntentKind, INTENT_PATTERN, MAX_INTENT_CHARS, SUBMIT, ANSWER: what a
      clip is for (intent).
    - VoiceAccepted, SpeakParams, AudioChunkFrame, AudioEndFrame, ClientFrame, VoiceFrame,
      VoiceRefusedFrame, read_client_frame, MAX_CHUNK_BYTES, MAX_MEDIA_TYPE_CHARS: the Landing
      Board's shapes (models).
    - VOICE_ROUTES, AUDIO_BODY, AUDIO_PATH, clip_from_body: ``POST /v1/chat/audio`` (route).
    - listen, Talk, Send, MAX_HOLD_CHUNKS, CHUNK_IDLE_S, HOLD_GRACE_S: push-to-talk on the chat
      socket (talk).
    - VoiceServices, VoiceRules: what hearing needs (services).
    - AudioNectar, InMemoryAudioNectar, KeptAudio, MAX_KEPT_CLIPS, MAX_KEPT_BYTES: the Nectar seam
      and its retention sweep (nectar).
    - Delivered, SpokenGoal, weigh_goal, deliver_goal, deliver_answer, deliver_chat, words_of,
      WORD_LIMITS, SPOKEN_GOAL_CLEARANCE: where the words go (deliver).
    - VoiceNotServedError, InvalidIntentError, ClipRefusedError, AudioOverBudgetError,
      TranscriptionFailedError, NothingHeardError, TranscriptTooLongError, PushToTalkError: the
      refusals (errors).
"""

from hivemind.entrance.voice.deliver import (
    SPOKEN_GOAL_CLEARANCE,
    WORD_LIMITS,
    Delivered,
    SpokenGoal,
    deliver_answer,
    deliver_chat,
    deliver_goal,
    weigh_goal,
    words_of,
)
from hivemind.entrance.voice.door import (
    TRANSCRIBE_TIMEOUT_S,
    VOICE_TIER,
    Utterance,
    hear,
    voice_of,
)
from hivemind.entrance.voice.errors import (
    AudioOverBudgetError,
    ClipRefusedError,
    InvalidIntentError,
    NothingHeardError,
    PushToTalkError,
    TranscriptionFailedError,
    TranscriptTooLongError,
    VoiceNotServedError,
)
from hivemind.entrance.voice.intent import (
    ANSWER,
    INTENT_PATTERN,
    MAX_INTENT_CHARS,
    SUBMIT,
    VoiceIntent,
    VoiceIntentKind,
)
from hivemind.entrance.voice.models import (
    MAX_CHUNK_BYTES,
    MAX_MEDIA_TYPE_CHARS,
    AudioChunkFrame,
    AudioEndFrame,
    ClientFrame,
    SpeakParams,
    VoiceAccepted,
    VoiceFrame,
    VoiceRefusedFrame,
    read_client_frame,
)
from hivemind.entrance.voice.nectar import (
    MAX_KEPT_BYTES,
    MAX_KEPT_CLIPS,
    AudioNectar,
    InMemoryAudioNectar,
    KeptAudio,
)
from hivemind.entrance.voice.route import AUDIO_BODY, AUDIO_PATH, VOICE_ROUTES, clip_from_body
from hivemind.entrance.voice.services import VoiceRules, VoiceServices
from hivemind.entrance.voice.talk import (
    CHUNK_IDLE_S,
    HOLD_GRACE_S,
    MAX_HOLD_CHUNKS,
    Send,
    Talk,
    listen,
)

__all__ = [
    "ANSWER",
    "AUDIO_BODY",
    "AUDIO_PATH",
    "CHUNK_IDLE_S",
    "HOLD_GRACE_S",
    "INTENT_PATTERN",
    "MAX_CHUNK_BYTES",
    "MAX_HOLD_CHUNKS",
    "MAX_INTENT_CHARS",
    "MAX_KEPT_BYTES",
    "MAX_KEPT_CLIPS",
    "MAX_MEDIA_TYPE_CHARS",
    "SPOKEN_GOAL_CLEARANCE",
    "SUBMIT",
    "TRANSCRIBE_TIMEOUT_S",
    "VOICE_ROUTES",
    "VOICE_TIER",
    "WORD_LIMITS",
    "AudioChunkFrame",
    "AudioEndFrame",
    "AudioNectar",
    "AudioOverBudgetError",
    "ClientFrame",
    "ClipRefusedError",
    "Delivered",
    "InMemoryAudioNectar",
    "InvalidIntentError",
    "KeptAudio",
    "NothingHeardError",
    "PushToTalkError",
    "Send",
    "SpeakParams",
    "SpokenGoal",
    "Talk",
    "TranscriptTooLongError",
    "TranscriptionFailedError",
    "Utterance",
    "VoiceAccepted",
    "VoiceFrame",
    "VoiceIntent",
    "VoiceIntentKind",
    "VoiceNotServedError",
    "VoiceRefusedFrame",
    "VoiceRules",
    "VoiceServices",
    "clip_from_body",
    "deliver_answer",
    "deliver_chat",
    "deliver_goal",
    "hear",
    "listen",
    "read_client_frame",
    "voice_of",
    "weigh_goal",
    "words_of",
]
