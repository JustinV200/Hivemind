"""Start a new Worker on the Warden's Cell and hand it a CellSession.

This is how a Warden starts that Worker and hands it the CellSession it needs.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles starting a new Worker on the Warden's Cell. Called by wardens' public API on behalf
    of whatever calls wardens itself; calls into sibling packages at Layer 5 or below, never
    back up into wardens' other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
