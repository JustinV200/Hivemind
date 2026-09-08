"""Provide the retrieval side of the Honey Store: querying Honey by relevance and clearance.

Honey here means already-ripened, labelled, retrievable knowledge.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package.
    Handles the retrieval side: querying already-ripened Honey. Called by honey_store's public
    API on behalf of whatever calls honey_store itself; calls into sibling packages at Layer 2
    or below, never back up into honey_store's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 7 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under honey_store.
    - .claude/roadmap.md phase 7 for the work that first populates it.

Public API: none yet; first populated in phase 7.
"""

# Appendix A.3: nothing is re-exported yet; phase 7 adds the first public name.
__all__: list[str] = []
