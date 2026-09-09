"""Hold one sub-package per model vendor or local server kind: the providers package.

These are the only modules in the whole workspace allowed to import a vendor LLM SDK or an HTTP
client aimed at a model server, so that swapping a provider never touches code above llm/. Each
sub-package (`anthropic/`, `openai_compat/`) owns exactly one `kind` string from
`[llm.providers.<name>]`'s manifest schema, and exposes only its `LLMProvider` implementation and
that implementation's config type -- a sub-package's own `mapping.py`/`client.py` (vendor wire
details) stay private to it, never re-exported here or further.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the llm package. Handles the only
    sub-package allowed to import a vendor LLM SDK or model-server HTTP client. Imported by the
    registry (`hivemind.llm.registry`, roadmap step 3.4) **by module**
    (`hivemind.llm.providers.anthropic`, `hivemind.llm.providers.openai_compat`), not through
    this face: the registry picks a sub-package by the manifest's `kind` string, so it names the
    sub-module directly rather than routing through a name collected here. This face exists for a
    human or a test reaching for "every provider implementation this workspace ships", not for
    the registry's own dispatch.

Key invariants:
    - Only an adapter's `LLMProvider` implementation and its config type are re-exported here
      (`AnthropicConfig`/`AnthropicProvider`, `OpenAICompatConfig`/`OpenAICompatProvider` today);
      a sub-package's wire-mapping and HTTP modules are never imported from outside that
      sub-package.
    - Importing this module has no side effect (codingrules section 5.5): no provider is
      constructed, no network touched, at import time.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under llm.
    - .claude/codingrules.md section 8.6 for the vendor-SDK-confinement rule this package exists
      to satisfy.
    - .claude/roadmap.md phase 3 for the work that first populated it (steps 3.6 and 3.7).
    - hivemind.llm.providers.anthropic for the hosted-Claude adapter.
    - hivemind.llm.providers.openai_compat for the OpenAI-compatible local-server adapter.

Public API:
    - AnthropicConfig, AnthropicProvider (hivemind.llm.providers.anthropic).
    - OpenAICompatConfig, OpenAICompatProvider (hivemind.llm.providers.openai_compat).
"""

from hivemind.llm.providers.anthropic import AnthropicConfig, AnthropicProvider
from hivemind.llm.providers.openai_compat import OpenAICompatConfig, OpenAICompatProvider

__all__ = [
    "AnthropicConfig",
    "AnthropicProvider",
    "OpenAICompatConfig",
    "OpenAICompatProvider",
]
