"""Hold Worker-level MaskTactic implementations: the tactics package.

One example is the prose tactic, which changes how a Worker writes while the Pheromone Mask (the
policy that makes a bee's actions look more human when active) is active.

Fits into the Hive:
    Layer 4 (roles that do the work), inside the workers package. Handles Worker-level
    MaskTactic implementations for the Pheromone Mask. Called by workers' public API on behalf
    of whatever calls workers itself; calls into sibling packages at Layer 4 or below, never
    back up into workers' other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 6 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under workers.
    - .claude/roadmap.md phase 6 for the work that first populates it.

Public API: none yet; first populated in phase 6.
"""

# Appendix A.3: nothing is re-exported yet; phase 6 adds the first public name.
__all__: list[str] = []
