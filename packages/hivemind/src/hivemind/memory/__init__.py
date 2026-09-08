"""Cover the Hive's hot and warm memory tiers.

Provides assembling hot state for an awake episode, relevance scoring, the Handoff model (a
resumable snapshot of a task's memory), compaction, Pins (memory a task marks as must-keep), Bee
Bread (the warm tier) and Cell Wax (the Queen's written cautions about one Cell).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.awake and
    wardens.awake when they assemble an awake episode. Calls into hivemind.common and
    hivemind.cell.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 4 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/roadmap.md phase 4 for the work that first populates this package.

Public API: none yet; first populated in phase 4.
"""

# Appendix A.3: nothing is re-exported yet; phase 4 adds the first public name.
__all__: list[str] = []
