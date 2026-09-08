"""Allocate a Nuc's own Cell resources to the model servers it hosts.

A Nuc is a colonized Real Cell with its own model server; allocating its Cell's resources to the
servers it hosts itself keeps it working while disconnected.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles allocating a Nuc's own Cell resources to the model servers it hosts. Called by
    wardens' public API on behalf of whatever calls wardens itself; calls into sibling packages
    at Layer 5 or below, never back up into wardens' other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 8 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/roadmap.md phase 8 for the work that first populates it.

Public API: none yet; first populated in phase 8.
"""

# Appendix A.3: nothing is re-exported yet; phase 8 adds the first public name.
__all__: list[str] = []
