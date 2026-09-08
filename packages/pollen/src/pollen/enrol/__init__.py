"""Handle the device's side of first-run registration with the Queen: the enrol package.

It reports the device's capabilities and Forage (its spare capacity) so the Queen can enrol it
as a Real Cell.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Handles the device's side of
    first-run registration with the Queen. Called by pollen's public API on behalf of whatever
    calls pollen itself; calls into waggle only.

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
