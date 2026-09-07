"""Handle what a Warden does while its Cell has no connection to the Queen.

This covers what continues, what pauses, and how the Warden catches the Queen up when the
connection returns.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles what a Warden does while its Cell has no connection to the Queen. Called by wardens'
    public API on behalf of whatever calls wardens itself; calls into sibling packages at Layer
    5 or below, never back up into wardens' other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 11 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/roadmap.md phase 11 for the work that first populates it.

Public API: none yet; first populated in phase 11.
"""

# Appendix A.3: nothing is re-exported yet; phase 11 adds the first public name.
__all__: list[str] = []
