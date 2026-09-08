"""Turn enrolled devices into Real Cells: the swarm package.

It holds the swarm registry, enrolment, heartbeat, PollenSession (a device's CellSession carried
over Waggle) and Nuc promotion (giving a colonized device its own model server so it keeps
working disconnected).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by workers.launch when a
    task needs a Real Cell. Calls into hivemind.cell and hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 11 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 3 row this package occupies.
    - .claude/roadmap.md phase 11 for the work that first populates this package.

Public API: none yet; first populated in phase 11.
"""

# Appendix A.3: nothing is re-exported yet; phase 11 adds the first public name.
__all__: list[str] = []
