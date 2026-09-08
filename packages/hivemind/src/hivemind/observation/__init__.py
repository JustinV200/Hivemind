"""Serve the Observation Hive's read side: pydantic read models and the API that serves them.

The read models include FleetView, CellView, ForageView and the rest, plus metrics. The front end
that renders these views lives separately, in packages/observation-web.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by the Observation Hive front end in
    packages/observation-web. Calls into memory, pheromone and honey_store directly for reads
    (section 8.11); writes go through queen.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 12 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 7 row this package occupies.
    - .claude/roadmap.md phase 12 for the work that first populates this package.

Public API: none yet; first populated in phase 12.
"""

# Appendix A.3: nothing is re-exported yet; phase 12 adds the first public name.
__all__: list[str] = []
