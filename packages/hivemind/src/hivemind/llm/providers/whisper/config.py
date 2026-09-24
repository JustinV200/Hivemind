"""Define WhisperConfig: how one in-process Whisper provider loads and runs its model.

`kind = "whisper_local"` (roadmap step 6.5a, ADR-0033) runs OpenAI's Whisper speech model inside
the Hive's own process through faster-whisper, an optional extra (`hivemind[whisper]`), so a Nuc
(a device that keeps working disconnected) or a Night Veil Cell (an isolated, local-models-only
Cell) can hear without a second server. `WhisperConfig` is the validated form of one such
provider: which model to load, where (the GPU when one is present, the CPU otherwise), at what
numeric precision, whether it may download weights at all (never, when the Hive is offline), and
how long a load or a transcription may take before the call gives up.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.whisper`.
    Built by the provider registry (`hivemind.llm.registry`) from one `[llm.providers.<name>]`
    row and its transcriber binding's model id; read by this package's `loader` and `provider`.
    Calls into `hivemind.llm.transcription` (for `TranscriptionCapabilities`) and pydantic only.

Key invariants:
    - Frozen and extras-forbidding (codingrules section 8.5); no model id is a default here:
      the id always comes from the manifest's `[llm.slots]` row (codingrules section 8.6).
    - `compute_type` None means "pick for the device": half precision on a GPU, 8-bit integers
      on a CPU (ADR-0033), resolved by `hivemind.llm.providers.whisper.loader.resolve_device`.
    - `local_files_only` True never touches the network: the registry sets it whenever `[llm]
      offline = true`, so an offline Hive loads only weights already on disk.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the device and precision rules.
    - hivemind.llm.providers.whisper.loader for where this config is turned into a live model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.transcription import TranscriptionCapabilities

DEFAULT_TIMEOUT_S = 120.0  # One transcription's ceiling; mirrors the manifest's provider default.
DEFAULT_LOAD_TIMEOUT_S = 900.0  # Fifteen minutes: a first run may download a few gigabytes of
# weights before loading them; later loads read the local cache in seconds.
COMPUTE_TYPE_PATTERN = r"^[a-z0-9_]+$"  # A precision name such as "int8" or "float16".

# Where the model runs: "auto" takes the GPU when one is present and the CPU otherwise.
WhisperDevice = Literal["auto", "cpu", "cuda"]

__all__ = [
    "COMPUTE_TYPE_PATTERN",
    "DEFAULT_LOAD_TIMEOUT_S",
    "DEFAULT_TIMEOUT_S",
    "WhisperConfig",
    "WhisperDevice",
]


class WhisperConfig(BaseModel):
    """One `[llm.providers.<name>]` row of kind `whisper_local`, plus the model it serves."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(
        min_length=1,
        description="What faster-whisper loads: a model size name, a model repository id, or a "
        "path to an already converted model directory. From the manifest, never a default.",
    )
    device: WhisperDevice = Field(
        default="auto", description="Where the model runs; 'auto' prefers a GPU when present."
    )
    compute_type: str | None = Field(
        default=None,
        pattern=COMPUTE_TYPE_PATTERN,
        description="The numeric precision to load at; None picks float16 on a GPU and int8 on "
        "a CPU.",
    )
    download_root: Path | None = Field(
        default=None,
        description="Where downloaded weights are cached; None keeps the library's own cache.",
    )
    local_files_only: bool = Field(
        default=False,
        description="True loads only weights already on disk and never downloads; set whenever "
        "the Hive is offline.",
    )
    cpu_threads: int = Field(
        default=0, ge=0, description="Threads for CPU inference; 0 lets the library choose."
    )
    timeout_s: float = Field(
        default=DEFAULT_TIMEOUT_S,
        gt=0,
        description="The longest one transcription may run before the call gives up, in seconds.",
    )
    load_timeout_s: float = Field(
        default=DEFAULT_LOAD_TIMEOUT_S,
        gt=0,
        description="The longest the first load (and any download) may take, in seconds.",
    )
    capabilities: TranscriptionCapabilities = Field(
        default_factory=TranscriptionCapabilities,
        description="What this provider declares: no native streaming, phrase-level segments, "
        "ten-minute clips and every language the model knows, unless overridden.",
    )
