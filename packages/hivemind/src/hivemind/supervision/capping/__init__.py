"""Define the Capping gate that every side-effecting action must clear before it can land.

A Proposal for such an action is checked against a Postcondition and a RiskTier before anything with
an effect outside a scratch directory is allowed through.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package.
    Handles the Capping gate a Proposal must clear before it can take effect. Called by
    supervision's public API on behalf of whatever calls supervision itself; calls into sibling
    packages at Layer 2 or below, never back up into supervision's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under supervision.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
