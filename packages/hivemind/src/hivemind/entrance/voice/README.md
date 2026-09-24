# hivemind.entrance.voice

Voice in at the Landing Board (roadmap step 10.5f, codingrules 8.15: "voice is transcribed at the
door"). An enrolled device may speak instead of typing, and whatever it says goes where the same
words typed would go: a goal to the Queen's goal requests, an answer to the question waiting on
the human, a chat line to her inbox as a `HumanMessage`. Nothing downstream of the door ever sees
audio.

## Two ways in, one door

```
POST /v1/chat/audio?intent=…            /v1/chat/stream (the chat socket)
 raw body + Content-Type                 audio_chunk … audio_chunk, audio_end{intent}
   │ gate: signed, APPROVED,               │ first frame: signed, APPROVED; each hold is
   │ entrance:submit +c2                    │ re-judged at its end (police) and bounded
   │ clip_from_body                         │ clip_from_chunks
   └──────────────► hear(services, caller, Utterance) ◄──┘
                     1. answer: entrance:answer, and the question is waiting
                     2. max_clip_seconds (WAV header, or the declared length)
                     3. goal: spend against the device's day (interactive: step up now)
                     4. audio budget: the clip's seconds, charged whole
                     5. transcribe once on TRANSCRIBER (registry → Fanner → one llm.call)
                     6. scan the words as a Landing Board message (10.6b)
                     7. deliver: goal (echoed and held) | answer | chat
                     8. keep the clip as C2 Nectar only with keep_audio; otherwise drop it
                      │
             202 VoiceAccepted  /  a `voice` frame on the speaking socket alone
```

Every step before 5 can refuse without a model: a pending, locked or revoked device never gets
past the gate (or, mid-hold, the re-judged session), a device without the intent's capability is
refused at the Entrance route point (`guard.denied`), and a clip too long or over the device's
budget is answered 413 or 429. A hold that breaks the protocol (a chunk too many, a format the Hive
does not take, an end with no audio, a missed deadline) is refused once on the socket and dropped;
the socket stays open for the chat.

## Intents

| Intent | Capability | Where the words go |
|---|---|---|
| `goal` | `entrance:submit` | A `GoalRequest` with `source = spoken`. With `confirm_goals` (the default) it is echoed back, in the answer and in the chat, and held AWAITING_CONFIRMATION until the device confirms it (`POST /v1/goals/{id}/confirm`) or declines it; nothing is planned or spent before. A goal the scanner flags is held so even with `confirm_goals` off. The typed goal's spend rules apply. |
| `answer:<question id>` | `entrance:submit`, `entrance:answer` | Straight to the waiting question, which resumes its task with no confirmation step. |
| `chat` | `entrance:submit` | A chat line, into the Queen's inbox as a `HumanMessage`. |

Every intent also needs `honey:clearance:c2`: the reply carries the transcript.

## Limits

- **Length.** `[entrance.voice] max_clip_seconds`, read from a WAV header, or from the declared
  `duration_s` of a compressed clip (Ogg or WebM Opus, MP3, M4A), which must be given. The
  transcription boundary's own ceilings (25 MB, 600 s) hold regardless.
- **Audio seconds.** A token bucket per device in the Entrance's `RateLimiter`, beside its request
  buckets: `audio_seconds_per_minute` seconds of capacity refilling at that rate, charged each
  clip's whole length before it is transcribed, so a clip it cannot pay for is refused with 429
  and never heard. The manifest refuses a budget below `max_clip_seconds`.
- **Push-to-talk.** One hold buffers at most `MAX_HOLD_CHUNKS` frames and 25 MB, lasts at most
  `max_clip_seconds` plus a grace, and waits at most `CHUNK_IDLE_S` between frames; a chunk frame
  carries at most 512 KiB (base64 in JSON).

## Audio and the transcript are C2

The transcript goes back only to the device that spoke. Neither the audio nor the words reach a log
line or the trail: the transcription's `llm.call` carries the slot, provider, latency and the clip's
seconds (`audio_s`), and a flag records a keyed hash of the words. By default the clip is dropped
once heard. With `keep_audio`, `hear` deposits it as `KeptAudio` (C2, its device, its reference:
the goal request or question it became, and `expires_at` = now + `keep_audio_hours`) through the
`AudioNectar` seam. The seam's implementation today is `InMemoryAudioNectar`: bounded (256 clips,
128 MiB; the oldest goes first) and swept once a minute by the Entrance's own sweep loop, which
deletes every clip past its window. Nothing is written to disk, so a restart forgets every kept
clip; a durable implementation takes the same seam once the Honey Store's Nectar intake (roadmap
7.4) exists.

## Voice off

With `[entrance.voice] enabled = false` the route is never mounted (`Switch.VOICE`), so it is a
404 on both listeners, like any row a listener does not serve (ADR-0034); the published contract
still describes it, with `x-hive-switch`. A push-to-talk hold is refused on the socket with the
same 404 (`hivemind.entrance.voice_not_served`), and the chat socket serves on.

## Public API

`hear`, `Utterance`, `voice_of` (the door); `VoiceIntent`, `VoiceIntentKind` (intents);
`VOICE_ROUTES`, `clip_from_body` (the route); `listen`, `Talk` (push-to-talk); `VoiceServices`,
`VoiceRules` (what `hive serve` builds); `AudioNectar`, `InMemoryAudioNectar`, `KeptAudio` (the
Nectar seam); the Landing Board's shapes (`VoiceAccepted`, `SpeakParams`, `AudioChunkFrame`,
`AudioEndFrame`, `VoiceFrame`, `VoiceRefusedFrame`); and the refusals. The face lists every name.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/voice \
    packages/hivemind/tests/e2e/test_voice_on_hive_serve.py
```

The unit tests run a real Entrance and Queen (`builders.entrance.serving`) with a
`FakeTranscription` whose `calls` record every clip a model heard, so "refused before any model
runs" is an empty list; `builders.entrance.voice` speaks a clip and holds the talk button.
`tests/e2e/test_voice_on_hive_serve.py` runs `hive serve`'s composition over real sockets with the
transcriber bound to a scripted OpenAI-compatible server: a phone speaks a goal (echoed, confirmed,
run) and answers the Drone's question by push-to-talk, and a byte run planted in each clip is found
in no database file, trail event or log line.
