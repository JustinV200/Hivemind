"""Provide the Pheromone Trail, the Hive's append-only audit log.

Every mutation anywhere in the Hive leaves a PheromoneEvent here, split into per-node segments
that sync when a Real Cell (an existing device the Hive borrows) reconnects.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by every layer above it, each time
    it mutates state. Calls into hivemind.common and waggle.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 2 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/roadmap.md phase 2 for the work that first populates this package.

Public API: none yet; first populated in phase 2.
"""

# Appendix A.3: nothing is re-exported yet; phase 2 adds the first public name.
__all__: list[str] = []
