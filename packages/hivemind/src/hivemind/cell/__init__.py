"""Define the Cell abstraction shared by every kind of machine the Hive runs work on.

Provides Cell, CellKind (REAL for a borrowed device or VIRTUAL for a provisioned one), CellSession
(a terminal session on it), leases, TaskNeeds (what a task requires from the Cell that runs it) and
the three security tier enums it defines: AccessLevel (what a session may do), CombShieldLevel (how
much of a tool's output a Cell may see) and HoneyClearance (how sensitive retrieved knowledge may
be). It knows what a Cell is, never how one is made.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by hive, swarm, exoskeleton
    and royal_jelly (Layer 3) and every layer above them. Calls into hivemind.common only; it
    defines the security enums guard interprets rather than importing them.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
