"""Manage a local Ollama server: list its models, pull one, hold one in memory, check its health.

Ollama is a local model server; chat with it goes through the one OpenAI-compatible adapter
(`hivemind.llm.providers.openai_compat`) like every other local server. What that door cannot do
-- listing installed models, downloading a model, choosing how long it stays loaded -- Ollama's
native API can, and this package wraps exactly that (roadmap step 8.2, ADR-0044). It is optional
and never a provider `kind`: a server is recognised as Ollama by probing its native API
(`OllamaManager.detect`), never by name, and every other server gets only the generic `/models`
health probe the chat adapter already has.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.providers`. Used by the CLI
    (`hive llm models`, `pull`, `keep-alive`, and the health column of `hive llm providers`),
    never by the registry or a Worker: model management is an operator action. `httpx` is
    imported here and nowhere else outside the chat adapters.

Key invariants:
    - No model id, host or port is a default; the native URL is the provider's `base_url` with a
      trailing `/v1` dropped.
    - Detection never raises; every other operation raises a typed `hivemind.llm.errors.LLMError`.
    - Importing this package has no side effect (codingrules 5.5).

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - hivemind.llm.providers.openai_compat for the chat door to the same server.
    - hivemind.cli.llm for the commands built on this package.

Public API:
    - OllamaManager: the real manager (`detect`, `create`, `open`, list/pull/keep_alive/health).
    - ModelServerManager, ManagerOpener, PullProgress, SUCCESS_STATUS: the Protocol, its opener
      type, the value a pull streams and the status its last line carries.
    - FakeOllamaManager, opener: the in-memory implementation and its `ManagerOpener` wrapper.
    - OllamaModel, OllamaHealth, OllamaLoadedModel: what the server reports.
"""

from hivemind.llm.providers.ollama.fake import FakeOllamaManager, opener
from hivemind.llm.providers.ollama.manage import OllamaManager
from hivemind.llm.providers.ollama.models import OllamaHealth, OllamaLoadedModel, OllamaModel
from hivemind.llm.providers.ollama.protocol import (
    SUCCESS_STATUS,
    ManagerOpener,
    ModelServerManager,
    PullProgress,
)

__all__ = [
    "SUCCESS_STATUS",
    "FakeOllamaManager",
    "ManagerOpener",
    "ModelServerManager",
    "OllamaHealth",
    "OllamaLoadedModel",
    "OllamaManager",
    "OllamaModel",
    "PullProgress",
    "opener",
]
