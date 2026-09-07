"""Represent the Queen herself: the Hive's single orchestrator and only global view.

It covers her inbox, Autopilot (deterministic fallback behaviour that never awaits a model) and
Awake (a bounded, stateless episode where she may think with a model) modes, planning, placement
(Real vs Virtual Cell), her share of Forage (the Hive's shared model-serving capacity), the
dispatcher, Clustering (pausing and preserving Hive state while a model provider is down),
Requeening (restoring a crashed Queen from her last state) and Supersedure (moving the Hive Stand
to another machine).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Called by entrance, observation
    and cli (Layer 7). Calls into wardens (Layer 5) and everything below it.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 6 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
