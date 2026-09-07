"""Define the Hive's policy engine: CapabilitySet and the access rules for AccessLevel.

CapabilitySet is what one bee is currently allowed to do; access.py defines what each AccessLevel,
one of the three Cell security tiers, permits. The security tier enums themselves live in
cell/tiers.py; guard only interprets them.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by every layer above it,
    before an action is allowed to proceed. Calls into the security enums hivemind.cell defines,
    and hivemind.common.

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
