"""Define the Exoskeleton's audio capability: the buzz package.

A protocol and backends hear and speak through a Cell.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Handles the audio capability: hearing and speaking through a Cell. Called by exoskeleton's
    public API on behalf of whatever calls exoskeleton itself; calls into sibling packages at
    Layer 3 or below, never back up into exoskeleton's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 6 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under exoskeleton.
    - .claude/roadmap.md phase 6 for the work that first populates it.

Public API: none yet; first populated in phase 6.
"""

# Appendix A.3: nothing is re-exported yet; phase 6 adds the first public name.
__all__: list[str] = []
