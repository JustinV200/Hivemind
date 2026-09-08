"""Define one read model per Observation Hive view.

The views cover the fleet, a single Cell, the Forage split, Attendant (the inbox triage every
supervisor uses) queues, Capping (the pre-effect safety gate a Proposal, a requested
side-effecting action, must clear) and the Honey (ripened, retrievable knowledge) browser.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the observation package. Handles the
    pydantic read models the observation API serves. Called by observation's public API on
    behalf of whatever calls observation itself; calls into sibling packages at Layer 7 or
    below, never back up into observation's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 12 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under observation.
    - .claude/roadmap.md phase 12 for the work that first populates it.

Public API: none yet; first populated in phase 12.
"""

# Appendix A.3: nothing is re-exported yet; phase 12 adds the first public name.
__all__: list[str] = []
