"""Represent the Warden: the per-Cell supervisor that spawns and supervises Workers on one Cell.

It covers Warden state, its inbox, its Autopilot (which never awaits a model) and Awake (a
bounded, stateless episode where it may think with a model) modes, spawning, its local model
pool, offline handling and read-only Watch mode (observing its Cell on a schedule instead of
sitting idle); a Warden never provisions Cells itself.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Called by queen's dispatcher,
    which assigns tasks to a Warden. Calls into workers (Layer 4) and everything below it.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 5 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
