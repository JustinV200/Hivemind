"""Decide whether a task's TaskNeeds are met by a Real Cell or a new Virtual one.

TaskNeeds are what a task requires from the Cell that runs it; this is the Queen's pure decision
of whether to reuse a Real Cell or provision a new Virtual one.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles the pure decision of Real vs Virtual Cell for a task's TaskNeeds. Called by queen's
    public API on behalf of whatever calls queen itself; calls into sibling packages at Layer 6
    or below, never back up into queen's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 5 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/roadmap.md phase 5 for the work that first populates it.

Public API: none yet; first populated in phase 5.
"""

# Appendix A.3: nothing is re-exported yet; phase 5 adds the first public name.
__all__: list[str] = []
