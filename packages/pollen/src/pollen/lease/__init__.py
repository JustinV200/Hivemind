"""Hold the device-side half of a Real Cell lease: the lease package.

Its scratch directory, the PIDs it started, how it restores the device on release, and a
dead-man switch if the Queen goes silent all live here.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Handles the device-side half
    of a Real Cell lease. Called by pollen's public API on behalf of whatever calls pollen
    itself; calls into waggle only.

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
