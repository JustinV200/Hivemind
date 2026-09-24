"""Transcribe in process with faster-whisper: the `whisper_local` provider kind (roadmap step 6.5a).

Whisper is OpenAI's open speech-recognition model; faster-whisper runs it on CTranslate2, on the
GPU when one is present and the CPU otherwise. This sub-package is the in-process adapter for
`ModelSlot.TRANSCRIBER` (the model slot that hears, ADR-0033): `WhisperConfig` (one
`[llm.providers.<name>]` row of `kind = "whisper_local"` plus its binding's model id, validated)
and `WhisperLocalTranscription` (the `hivemind.llm.transcription.TranscriptionProvider` built from
it). faster-whisper is an optional extra (`hivemind[whisper]`), imported only by `loader`, and
only when a model is first needed, so this package (and the whole Hive) imports without it.
`ModelLoader`/`LoadedModel` are exported as the adapter's one seam, so a test or demo can load a
stand-in model instead of a real one.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers`. Imported
    by the provider registry (`hivemind.llm.registry`) by module; nothing above `hivemind.llm`
    imports it. Calls into `hivemind.llm.transcription`, `hivemind.llm.errors`,
    `hivemind.llm.capabilities` and `waggle.clock`; `faster_whisper`, `ctranslate2` and `av`
    only inside `loader`, lazily.

Key invariants:
    - Importing this package never imports faster-whisper (codingrules section 5.5 applied to an
      optional extra); only `FasterWhisperLoader.load` does.
    - `mapping` is the only module where a faster-whisper field or option name appears
      (codingrules section 8.6).

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the decision this implements.
    - hivemind.llm.providers.whisper.README for configuration, devices and how to test.
    - hivemind.llm.transcription for the protocol and the values this package speaks.

Public API:
    - WhisperConfig: this provider's validated configuration.
    - WhisperLocalTranscription: the TranscriptionProvider, one seat per loaded model.
    - ModelLoader, LoadedModel, FasterWhisperLoader: the loading seam and its real implementation.
"""

from hivemind.llm.providers.whisper.config import WhisperConfig
from hivemind.llm.providers.whisper.loader import FasterWhisperLoader, LoadedModel, ModelLoader
from hivemind.llm.providers.whisper.provider import WhisperLocalTranscription

__all__ = [
    "FasterWhisperLoader",
    "LoadedModel",
    "ModelLoader",
    "WhisperConfig",
    "WhisperLocalTranscription",
]
