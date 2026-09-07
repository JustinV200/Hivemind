"""Queue questions, Alarms and task events for the Queen's Attendant triage.

Every Warden's questions, Alarms and task events queue here, for Attendant (the inbox triage
every supervisor uses), before the Queen acts.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles where questions, Alarms and task events queue up for Attendant triage. Called by
    queen's public API on behalf of whatever calls queen itself; calls into sibling packages at
    Layer 6 or below, never back up into queen's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
