"""Issue and consume device invites for the Hive Entrance.

Approval of a new device happens only on the loopback listener, never remotely.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the entrance package. Handles device
    invites, approved only from the loopback listener. Called by entrance's public API on behalf
    of whatever calls entrance itself; calls into sibling packages at Layer 7 or below, never
    back up into entrance's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 10 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under entrance.
    - .claude/roadmap.md phase 10 for the work that first populates it.

Public API: none yet; first populated in phase 10.
"""

# Appendix A.3: nothing is re-exported yet; phase 10 adds the first public name.
__all__: list[str] = []
