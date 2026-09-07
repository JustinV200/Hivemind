"""Provision and destroy Virtual Cells: VM or container Cells the Queen owns, not borrows.

This is the hive package (lowercase, distinct from the Hive as a whole). It covers their lifecycle,
Night Veil attestation (the always-teardown-only security tier) and the Overwintering pool that
keeps a dormant Cell around for fast reuse.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by workers.launch, and the
    wardens supervising the Cells it makes. Calls into hivemind.cell and hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 5 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 3 row this package occupies.
    - .claude/roadmap.md phase 5 for the work that first populates this package.

Public API: none yet; first populated in phase 5.
"""

# Appendix A.3: nothing is re-exported yet; phase 5 adds the first public name.
__all__: list[str] = []
