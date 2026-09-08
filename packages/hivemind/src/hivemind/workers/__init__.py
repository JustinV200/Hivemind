"""Provide the Worker runtime: the workers package.

It holds the roles that do the Hive's actual work (Forager, Scout, GuardBee, Undertaker, Drone,
HouseBee), the tools they call, and the tactics they can invoke, such as writing in a more human
style under the Pheromone Mask.

Fits into the Hive:
    Layer 4 (roles that do the work). Called by wardens.spawn. Calls into hive, swarm,
    exoskeleton and royal_jelly (Layer 3) and everything below them.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 4 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
