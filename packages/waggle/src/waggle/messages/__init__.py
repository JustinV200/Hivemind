"""Hold one module per Waggle message family: the messages package.

Task, cell, honey, capping, and so on: each family is a pydantic model describing one kind of
payload that can travel inside an Envelope (the outer wrapper every Waggle message travels in).
Keeping one file per family means a reader who wants to know what a Cell message looks like
opens exactly one small file.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Handles one pydantic model per Waggle message family carried inside an Envelope. Called by
    waggle's public API on behalf of whatever calls waggle itself; calls into nothing else in
    the workspace.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 1 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under waggle.
    - .claude/roadmap.md phase 1 for the work that first populates it.

Public API: none yet; first populated in phase 1.
"""

# Appendix A.3: nothing is re-exported yet; phase 1 adds the first public name.
__all__: list[str] = []
