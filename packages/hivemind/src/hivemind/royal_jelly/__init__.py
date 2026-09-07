"""Provide the Royal Jelly Lab and Comb Registry: the royal_jelly package.

It defines how a Worker or Warden requests a brand-new tool, has it scaffolded and quarantined,
and, once approved, promoted into the registry at hive or Cell scope.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by a Worker or Warden that
    needs a tool that does not exist yet. Calls into hivemind.cell, hivemind.guard and
    hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 9 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 3 row this package occupies.
    - .claude/roadmap.md phase 9 for the work that first populates this package.

Public API: none yet; first populated in phase 9.
"""

# Appendix A.3: nothing is re-exported yet; phase 9 adds the first public name.
__all__: list[str] = []
