"""Load and validate the Hive Manifest, the Hive's TOML configuration file.

The Manifest describes one Hive (a running instance of the whole system): which backend
provisions Virtual Cells, which model providers are allowed, and similar operator choices. Every
field is validated by a pydantic model before anything else in the Hive reads it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by the composition root, then read
    by every layer above. Calls into hivemind.common and waggle.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
