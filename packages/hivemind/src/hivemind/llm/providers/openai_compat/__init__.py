"""Speak the OpenAI-compatible chat-completions wire to a local or self-hosted model server.

An "OpenAI-compatible server" is any process exposing the same `/chat/completions` and `/models`
HTTP shape OpenAI's own hosted API does, without being that API: llama.cpp's built-in server,
Ollama, vLLM and LM Studio are the ones this package's README names as tested against. This
sub-package is the adapter roadmap step 3.7 asks for: `OpenAICompatConfig` (the manifest's
`[llm.providers.<name>]` section, of `kind = "openai_compat"`, turned into a validated value) and
`OpenAICompatProvider` (the `hivemind.llm.provider.LLMProvider` implementation built from it).
`mapping.py` and `client.py`, this package's other two modules, are private: neither an OpenAI
wire field name nor an `httpx` type is meant to be seen from outside this package.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers`. Imported
    by the registry (`hivemind.llm.registry`, roadmap step 3.4) by module, never re-exported
    further than `hivemind.llm.providers`'s own face (see that package's `__init__.py`). Calls
    into `hivemind.llm.capabilities`, `hivemind.llm.errors`, `hivemind.llm.models`,
    `hivemind.forage.slots` and `waggle.clock`; `httpx` only inside this package (codingrules
    section 8.6).

Key invariants:
    - No name beyond `OpenAICompatConfig`/`OpenAICompatProvider` is exported here; `mapping`'s and
      `client`'s contents are this package's own implementation detail.
    - Importing this module has no side effect: constructing a provider (`OpenAICompatProvider.
      create`) is the composition root's job, not import time (codingrules section 5.5).

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules this implements.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision behind the
      LLMProvider Protocol this package implements.
    - hivemind.llm.providers.openai_compat.README for which servers this adapter speaks to, how
      capabilities are declared, and how to run a live test later.
    - hivemind.llm.provider for the LLMProvider Protocol OpenAICompatProvider implements.

Public API:
    - OpenAICompatConfig: this provider's validated configuration.
    - OpenAICompatProvider: the LLMProvider implementation, with `.create(name, config, clock)`.
"""

from hivemind.llm.providers.openai_compat.provider import OpenAICompatConfig, OpenAICompatProvider

__all__ = ["OpenAICompatConfig", "OpenAICompatProvider"]
