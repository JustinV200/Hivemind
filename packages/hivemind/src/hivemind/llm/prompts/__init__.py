"""Re-export the prompts package's public surface: named prompt assets and their assembly.

The Queen and her bees assemble these plain-markdown templates into an awake episode (a bounded,
stateless turn where a bee is allowed to think with a model). Prompts live here, not scattered
through the callers, so tone and structure stay consistent across the Hive, and so a portability
rule (codingrules section 8.6: no vendor tags, no model ids) has exactly one place to hold.

Fits into the Hive:
    Layer 1 (foundational services), inside hivemind.llm. Called by hivemind.memory.assemble (a
    parallel roadmap step) once it turns durable state into labelled sections, and by every awake
    episode (queen/awake, wardens/awake) and the Drone's tool loop, which pass the assembled text
    as an LLMRequest's `system` field. Calls into hivemind.common only.

Key invariants:
    - Every PromptName maps to exactly one `<name>.md` file shipped inside this package; a missing
      file raises PromptNotFoundError rather than a bare FileNotFoundError.
    - render() always orders sections PINS, HOT_STATE, RETRIEVED, USER, EVENT regardless of the
      order the caller's mapping iterates in (codingrules 8.9, "stable prefix first").

See Also:
    - .claude/codingrules.md section 8.6 for "Prompts are portable".
    - .claude/codingrules.md section 8.9 for "Stable prefix first".
    - .claude/codingrules.md section 15 for labelling retrieved/hot-state content as data.
    - hivemind.llm.prompts.loader for the implementation these names come from.

Public API:
    - PromptName: which shipped prompt asset to load (hivemind.llm.prompts.loader).
    - SectionLabel: what kind of durable state one rendered section carries.
    - load_prompt: read one prompt's markdown body.
    - render: assemble a prompt body plus its labelled sections in stable-prefix order.
"""

from hivemind.llm.prompts.loader import PromptName, SectionLabel, load_prompt, render

__all__ = ["PromptName", "SectionLabel", "load_prompt", "render"]
