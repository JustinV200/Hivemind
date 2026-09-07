"""Hold the templates assembled into an awake episode: the prompts package.

The Queen and her bees assemble these templates into an awake episode (a bounded, stateless turn
where a bee is allowed to think with a model). Prompts live here, not scattered through the
callers, so tone and structure stay consistent across the Hive.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the llm package. Handles the
    prompt templates an awake episode is assembled from. Called by llm's public API on behalf of
    whatever calls llm itself; calls into sibling packages at Layer 1 or below, never back up
    into llm's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under llm.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
