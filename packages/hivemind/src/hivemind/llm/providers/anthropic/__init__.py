"""Speak the Anthropic Messages API wire to hosted Claude models.

Anthropic (the vendor behind the Claude family of models) is the Hive's first LLM provider: the
README's own bet is that the Hive starts on Claude and must be able to move, slot by slot, to a
locally hosted model without touching any code above `hivemind.llm`. This sub-package is the
adapter roadmap step 3.6 asks for: `AnthropicConfig` (one `[llm.providers.<name>]` manifest
section of kind `"anthropic"`, turned into a validated value) and `AnthropicProvider` (the
`hivemind.llm.provider.LLMProvider` implementation built from it), with adaptive thinking,
streaming, native structured output, `strict` tool schemas, prompt-caching breakpoints and API
token counting. `mapping.py`, `streaming.py` and `client.py`, this package's other modules, are
private: neither an Anthropic wire field name nor an `anthropic` SDK type is meant to be seen from
outside this package.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers`. Imported
    by the registry (`hivemind.llm.registry`, roadmap step 3.4) by module, never re-exported
    further than `hivemind.llm.providers`'s own face (see that package's `__init__.py`). Calls
    into `hivemind.llm.capabilities`, `hivemind.llm.errors`, `hivemind.llm.models`,
    `hivemind.forage.slots` and `waggle.clock`; the `anthropic` SDK only inside this package
    (codingrules section 8.6).

Key invariants:
    - No name beyond `AnthropicConfig`/`AnthropicProvider` is exported here; `mapping`'s,
      `streaming`'s and `client`'s contents are this package's own implementation detail.
    - Importing this module has no side effect: constructing a provider
      (`AnthropicProvider.from_config`) is the composition root's job, not import time
      (codingrules section 5.5).

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules this implements.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision behind the
      LLMProvider Protocol this package implements.
    - hivemind.llm.providers.anthropic.README for capability declaration and error mapping.
    - hivemind.llm.provider for the LLMProvider Protocol AnthropicProvider implements.

Public API:
    - AnthropicConfig: this provider's validated configuration.
    - AnthropicProvider: the LLMProvider implementation, with `.from_config(name, config, clock)`.
"""

from hivemind.llm.providers.anthropic.provider import AnthropicConfig, AnthropicProvider

__all__ = ["AnthropicConfig", "AnthropicProvider"]
