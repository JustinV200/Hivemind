"""Provide the local Cell backend for the Hive Stand, the Queen's own machine and first Real Cell.

It doubles as the default home of every Warden (the per-Cell supervisor) and needs nothing beyond
the standard library.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the cell package. Handles the
    Hive Stand, the one Real Cell the local backend never has to provision. Called by cell's
    public API on behalf of whatever calls cell itself; calls into sibling packages at Layer 2
    or below, never back up into cell's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under cell.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
