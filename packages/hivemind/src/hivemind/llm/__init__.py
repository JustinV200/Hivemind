"""Define the provider protocols and routing every model call in the Hive goes through: llm.

The LLMProvider and EmbeddingProvider protocols let the rest of the Hive talk to a model; slot
resolution, ladders, routing and the Fanner (the seat meter every model call passes through) live
here too. It is provider-agnostic: no vendor SDK is imported outside llm/providers/, and code
above this package sees only our own request, response and capability models.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by workers, wardens and queen
    whenever a bee is allowed to think with a model. Calls into hivemind.forage (never the
    reverse) and hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
