"""Hold one module per Worker role: the roles package.

Forager, Scout, GuardBee, Undertaker, Drone and HouseBee each implement the shared Worker
protocol.

Fits into the Hive:
    Layer 4 (roles that do the work), inside the workers package. Handles one module per Worker
    role implementing the shared Worker protocol. Called by workers' public API on behalf of
    whatever calls workers itself; calls into sibling packages at Layer 4 or below, never back
    up into workers' other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under workers.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
