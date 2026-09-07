"""Provide Requeening: restore a crashed Queen without silently orphaning any task.

Restoring her also restores her outstanding leases and Forage (the Hive's shared model-serving
capacity) grants.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles restoring a crashed Queen from her last persisted state. Called by queen's public
    API on behalf of whatever calls queen itself; calls into sibling packages at Layer 6 or
    below, never back up into queen's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 13 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/roadmap.md phase 13 for the work that first populates it.

Public API: none yet; first populated in phase 13.
"""

# Appendix A.3: nothing is re-exported yet; phase 13 adds the first public name.
__all__: list[str] = []
