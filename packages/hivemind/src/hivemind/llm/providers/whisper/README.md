# hivemind.llm.providers.whisper

Runs OpenAI's Whisper speech model inside the Hive's own process, through
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) on CTranslate2, for
`ModelSlot.TRANSCRIBER` (roadmap step 6.5a,
`docs/adr/0033-transcription-provider-whisper-first.md`). This is the `kind = "whisper_local"`
provider: a Nuc, a Night Veil Cell or an offline Hive can hear without running a second server.

## Public API

- **`WhisperConfig`**: one `[llm.providers.<name>]` row of kind `whisper_local` plus its binding's
  model id. `model` (a size name, a model repository id, or a converted model directory; always
  from the manifest), `device` (`"auto"`, `"cpu"`, `"cuda"`), `compute_type` (`None` picks
  `float16` on a GPU, `int8` on a CPU), `download_root`, `local_files_only` (set by the registry
  whenever `[llm] offline = true`), `cpu_threads`, `timeout_s` (one transcription), `load_timeout_s`
  (the first load, which may download weights) and `capabilities`
  (`hivemind.llm.transcription.TranscriptionCapabilities`).
- **`WhisperLocalTranscription`**: the `TranscriptionProvider`. Construction loads nothing; the
  first `transcribe` loads the model on a worker thread, and every run happens on one too
  (`asyncio.to_thread`, under the two timeouts). One loaded model is **one seat**: an
  `asyncio.Lock` lets one transcription use it at a time. `stream` buffers the chunks and
  transcribes once (faster-whisper decodes whole files). `health()` never loads: `DOWN` without the
  extra or after a failed load, `HEALTHY` otherwise.
- **`ModelLoader`**, **`LoadedModel`**, **`FasterWhisperLoader`**: the adapter's one seam onto the
  library. `FasterWhisperLoader` is the real one; a test passes its own loader that returns a
  stand-in model, so no model is ever downloaded in a test.

## The optional extra

faster-whisper pulls a native inference runtime a terminal-only Hive never needs, so it is the
`hivemind[whisper]` extra (`uv sync --extra whisper`). Only `loader.py` imports it (and
`ctranslate2` and `av`), lazily, inside functions: the package, the registry and the whole Hive
import fine without it. With the extra absent, the first transcription raises
`ProviderUnavailableError` naming the extra, so a manifest fallback (for example an
`openai_compat` server) takes over, and `health()` reports `DOWN` with the same hint.

## Devices and precision

`device = "auto"` asks CTranslate2 how many CUDA GPUs it can use: the GPU at `float16` when there is
one, the CPU at `int8` otherwise (ADR-0033). No model size is chosen in code; the shipped example
manifests recommend `large-v3-turbo` with a GPU and `small` on a CPU until the eval harness (8.5)
measures them.

## Seats

A `whisper_local` provider holds one model per (provider, model id) pair and serves one
transcription at a time, so give its manifest row `seats = 1`: the Fanner then queues extra callers
by tempo in its own seat meter instead of letting them wait unseen on the adapter's lock.

## Error mapping

| Where | Library error | Raised as |
|---|---|---|
| Load | `ImportError` (the extra is absent) | `ProviderUnavailableError`, naming the extra |
| Load | `OSError`, `RuntimeError`, `ValueError` (model not on disk or not downloadable, unusable device or precision) | `ProviderUnavailableError` |
| Load or run | past `load_timeout_s` / `timeout_s` | `ProviderUnavailableError` |
| Run | `ValueError`, a PyAV codec error (an unknown language code, undecodable audio) | `ProviderRequestError` (400, `audio_refused`) |
| Run | `OSError`, `RuntimeError`, `MemoryError` (device lost, out of memory) | `ProviderUnavailableError` |
| Run | output that breaks a transcript bound (runaway text) | `MalformedOutputError`, quoting no words |

A run that times out keeps its worker thread until the library returns (a thread cannot be
killed); the library queues any overlapping run on the same model, so a late run delays the next
one and never corrupts it.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm/providers/whisper
uv run --frozen pytest packages/hivemind/tests/contracts/test_transcription_provider_contract.py
```

Unit and contract tests hand the adapter `builders.audio.StandInLoader` (a stand-in model with the
library's own segment field names) and generated WAV clips; nothing is downloaded. One
`local_llm` test runs a real model already on disk:

```bash
HIVEMIND_LOCAL_WHISPER_MODEL=<a cached size name or a converted model directory> \
    uv run --frozen pytest -m local_llm \
    packages/hivemind/tests/contracts/test_transcription_provider_contract.py
```

It loads with `local_files_only`, so a model that is not on disk is skipped, never fetched.
