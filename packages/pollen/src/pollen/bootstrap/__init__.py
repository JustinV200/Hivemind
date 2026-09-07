"""Install the full hivemind runtime onto a device promoted to a Nuc.

A Nuc is a Real Cell with its own model server, and this package carries a colonized Real Cell
through the Queen's promotion of it to that role.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Handles installing the full
    hivemind runtime when a device is promoted to a Nuc. Called by pollen's public API on behalf
    of whatever calls pollen itself; calls into waggle only.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 11 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under pollen.
    - .claude/roadmap.md phase 11 for the work that first populates it.

Public API: none yet; first populated in phase 11.
"""

# Appendix A.3: nothing is re-exported yet; phase 11 adds the first public name.
__all__: list[str] = []
