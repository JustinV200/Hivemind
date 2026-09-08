"""Serve the Hive Entrance: loopback and remote listeners guarding the Landing Board.

Two listeners, loopback (always on) and remote (only when explicitly exposed), serve the Landing
Board (the versioned public API contract), device enrolment, auth, push delivery, exposure control
and the human inbox. Approval routes never exist on the remote listener.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an enrolled device or the Observation
    Hive front end, over HTTP. Calls into queen (Layer 6) and below.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 10 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 7 row this package occupies.
    - .claude/roadmap.md phase 10 for the work that first populates this package.

Public API: none yet; first populated in phase 10.
"""

# Appendix A.3: nothing is re-exported yet; phase 10 adds the first public name.
__all__: list[str] = []
