"""Define the QuarantineComb: the quarantine_comb package.

It is an isolated place a newly scaffolded tool is exercised and observed before it can be
promoted.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Handles the isolated place a newly scaffolded tool is exercised before trust. Called by
    royal_jelly's public API on behalf of whatever calls royal_jelly itself; calls into sibling
    packages at Layer 3 or below, never back up into royal_jelly's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 9 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under royal_jelly.
    - .claude/roadmap.md phase 9 for the work that first populates it.

Public API: none yet; first populated in phase 9.
"""

# Appendix A.3: nothing is re-exported yet; phase 9 adds the first public name.
__all__: list[str] = []
