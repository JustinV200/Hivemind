"""Model capacity as data, including the ModelSlot and Tempo types llm depends on: forage.

HostCapacity, a Seat, RoleFootprint, ForageGrant and ForageRequest, the Forage map of every
source that can serve a model, and the ModelSlot (a named place in a routing ladder a model call
can resolve to) and Tempo (the speed-versus-accuracy dial for a slot) types all live here. It
never imports llm, so a grant or a routing input can name a model slot without pulling in the
provider machinery.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by queen when it divides capacity
    and llm when it resolves a slot. Calls into hivemind.common and waggle only; it never
    imports hivemind.llm.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 4 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/roadmap.md phase 4 for the work that first populates this package.

Public API: none yet; first populated in phase 4.
"""

# Appendix A.3: nothing is re-exported yet; phase 4 adds the first public name.
__all__: list[str] = []
