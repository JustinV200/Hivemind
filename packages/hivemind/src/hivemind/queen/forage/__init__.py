"""Divide the Hive's shared Forage pool among Wardens on the Queen's behalf.

This package is queen-scoped, distinct from the Layer 1 forage package, and sets ceilings per
Warden plus hosting plans for where each model runs.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles how the Queen divides the shared Forage pool among Wardens. Called by queen's public
    API on behalf of whatever calls queen itself; calls into sibling packages at Layer 6 or
    below, never back up into queen's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 4 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/roadmap.md phase 4 for the work that first populates it.

Public API: none yet; first populated in phase 4.
"""

# Appendix A.3: nothing is re-exported yet; phase 4 adds the first public name.
__all__: list[str] = []
