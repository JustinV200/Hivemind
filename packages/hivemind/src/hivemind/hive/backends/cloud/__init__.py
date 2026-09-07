"""Hold the Hive's per-cloud-provider CellBackend implementations.

Each cloud provider the Hive can provision a Virtual Cell on gets one CellBackend implementation
here.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the hive package. Handles
    one CellBackend implementation per cloud provider. Called by hive's public API on behalf of
    whatever calls hive itself; calls into sibling packages at Layer 3 or below, never back up
    into hive's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 5 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under hive.
    - .claude/roadmap.md phase 5 for the work that first populates it.

Public API: none yet; first populated in phase 5.
"""

# Appendix A.3: nothing is re-exported yet; phase 5 adds the first public name.
__all__: list[str] = []
