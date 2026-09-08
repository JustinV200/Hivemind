"""Provide the Hive's optional Cell peripherals: the Exoskeleton.

It bundles compound_eye (vision), antennae (input), buzz (audio), a browser attachment, and the
tactics a Worker can invoke, such as the input tactic for the Pheromone Mask (the policy that
makes a bee's actions look more human when active). Nothing in the core assumes any of this is
attached; a task asks for it explicitly.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by the worker role that
    requested it for a Cell. Calls into hivemind.cell and hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 6 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 3 row this package occupies.
    - .claude/roadmap.md phase 6 for the work that first populates this package.

Public API: none yet; first populated in phase 6.
"""

# Appendix A.3: nothing is re-exported yet; phase 6 adds the first public name.
__all__: list[str] = []
