"""Speak the OpenAI-compatible /audio/transcriptions wire: the openai_compat transcriber (6.5a).

Hosted Whisper APIs and local speech servers accept the same multipart
`POST /audio/transcriptions`, so one adapter covers them all (ADR-0033). This sub-package holds
it, beside the chat adapter it shares a manifest kind with: `OpenAICompatTranscriptionConfig`
(one `[llm.providers.<name>]` row of `kind = "openai_compat"` bound on `ModelSlot.TRANSCRIBER`,
the model slot that hears, plus that binding's model id) and `OpenAICompatTranscription` (the
`hivemind.llm.transcription.TranscriptionProvider` built from it). A sub-package rather than
prefixed siblings of the chat modules (codingrules 5.2): the transcriber needs its own client
(multipart, not JSON) and its own mapping (a different wire), each one concept.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside
    `hivemind.llm.providers.openai_compat`. Imported by the provider registry
    (`hivemind.llm.registry`) through `hivemind.llm.providers.openai_compat`'s face. Calls into
    `hivemind.llm.transcription`, `hivemind.llm.errors`, `hivemind.llm.capabilities`,
    `hivemind.llm.models` and `waggle.clock`; `httpx` only inside this sub-package.

Key invariants:
    - Only the config and the provider are exported; `client` and `mapping` are private, so no
      wire field name and no httpx type is visible outside (codingrules section 8.6).
    - Importing this package opens no connection (codingrules section 5.5).

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision this implements.
    - hivemind.llm.providers.openai_compat.README for the wire, the replies and error mapping.

Public API:
    - OpenAICompatTranscriptionConfig: this transcriber's validated configuration.
    - OpenAICompatTranscription: the TranscriptionProvider, with `.create(name, config, clock)`.
"""

from hivemind.llm.providers.openai_compat.transcription.provider import (
    OpenAICompatTranscription,
    OpenAICompatTranscriptionConfig,
)

__all__ = ["OpenAICompatTranscription", "OpenAICompatTranscriptionConfig"]
