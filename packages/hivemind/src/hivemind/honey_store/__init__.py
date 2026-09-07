"""Define the Honey Store, the Hive's cold-tier knowledge base.

Raw Nectar (unprocessed captured information) is taken in, ripened through a pipeline into Honey
(retrievable, labelled knowledge), and retrieved by Workers before they act.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by workers before they act
    (retrieval) and as they capture Nectar (intake). Calls into hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 7 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/roadmap.md phase 7 for the work that first populates this package.

Public API: none yet; first populated in phase 7.
"""

# Appendix A.3: nothing is re-exported yet; phase 7 adds the first public name.
__all__: list[str] = []
