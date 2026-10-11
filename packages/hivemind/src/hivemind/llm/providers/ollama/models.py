"""Define the frozen models for what an Ollama server's native API returns.

Ollama is a local model server (it runs open-weight models on the operator's own machine). Besides
the OpenAI-compatible door every local server shares, it has a native API for the things that door
cannot do: list the models on disk (`GET /api/tags`), report the loaded ones (`GET /api/ps`) and
its version (`GET /api/version`). This module is the only place those wire field names appear
(codingrules section 8.6: a provider's `mapping.py` equivalent). Models read what the server sent
and ignore fields this module does not name, because Ollama adds fields between releases and an
unknown field is not an error in someone else's server; a field this module does need and the
server omits is.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers.ollama`. Built by
    `manage.OllamaManager` from parsed JSON; read by `hivemind.cli.llm` (`hive llm models`,
    `hive llm providers`) through the manager's return values.

Key invariants:
    - Every model is frozen (codingrules 8.5); `extra="ignore"` is deliberate (see above) and is
      the one place this package departs from the boundary-model default of `extra="forbid"`.
    - Sizes are bytes, as Ollama reports them; no unit conversion happens in this module.

See Also:
    - hivemind.llm.providers.ollama.manage for the manager that produces these.
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for why management is
      Ollama-only.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "OllamaHealth",
    "OllamaLoadedModel",
    "OllamaModel",
    "OllamaModelDetails",
    "OllamaProcesses",
    "OllamaTags",
    "OllamaVersion",
]

_WIRE_CONFIG = ConfigDict(frozen=True, extra="ignore")  # Tolerate fields a newer server adds.


class OllamaModelDetails(BaseModel):
    """The `details` object of one installed model: what kind of model it is."""

    model_config = _WIRE_CONFIG

    family: str = Field(default="", description="The model family, e.g. the architecture name.")
    parameter_size: str = Field(default="", description="Parameter count as the server words it.")
    quantization_level: str = Field(default="", description="The weight quantisation, if any.")


class OllamaModel(BaseModel):
    """One installed model, as listed by `GET /api/tags`."""

    model_config = _WIRE_CONFIG

    name: str = Field(description="The model's tag, the id to pass back to the server.")
    size: int = Field(default=0, ge=0, description="Bytes the model occupies on disk.")
    digest: str = Field(default="", description="Content digest of the model's manifest.")
    modified_at: str = Field(default="", description="When the model last changed, as sent.")
    details: OllamaModelDetails = Field(
        default_factory=OllamaModelDetails, description="Family, parameter size and quantisation."
    )


class OllamaTags(BaseModel):
    """The `GET /api/tags` reply: every installed model."""

    model_config = _WIRE_CONFIG

    models: tuple[OllamaModel, ...] = Field(default=(), description="Installed models.")


class OllamaLoadedModel(BaseModel):
    """One model currently loaded in memory, as listed by `GET /api/ps`."""

    model_config = _WIRE_CONFIG

    name: str = Field(description="The loaded model's tag.")
    size_vram: int = Field(default=0, ge=0, description="Bytes of it resident in GPU memory.")
    expires_at: str | None = Field(
        default=None, description="When the server will unload it, as sent; None if unstated."
    )


class OllamaProcesses(BaseModel):
    """The `GET /api/ps` reply: every model loaded right now."""

    model_config = _WIRE_CONFIG

    models: tuple[OllamaLoadedModel, ...] = Field(default=(), description="Loaded models.")


class OllamaVersion(BaseModel):
    """The `GET /api/version` reply; its presence is how an Ollama server is recognised."""

    model_config = _WIRE_CONFIG

    version: str = Field(description="The server's version string.")


class OllamaHealth(BaseModel):
    """What `OllamaManager.health` reports: reachable, which version, what is loaded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(description="The server's version string.")
    loaded: tuple[OllamaLoadedModel, ...] = Field(
        description="Models resident in memory right now."
    )
