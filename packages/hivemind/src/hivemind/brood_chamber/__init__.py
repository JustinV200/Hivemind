"""Persist the task graph and its state machine: the Brood Chamber.

The Brood Chamber is the Hive's task store, holding the task graph and its state machine in
SQLite. Every task the Queen decomposes a goal into lives here for the rest of its life.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.planner, which
    persists the task graph here, and the dispatcher, which reads it. Calls into
    hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 2 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/roadmap.md phase 2 for the work that first populates this package.

Public API: none yet; first populated in phase 2.
"""

# Appendix A.3: nothing is re-exported yet; phase 2 adds the first public name.
__all__: list[str] = []
