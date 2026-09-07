"""Hold one sub-package per model vendor or local server kind: the providers package.

These are the only modules in the whole workspace allowed to import a vendor LLM SDK or an HTTP
client aimed at a model server, so that swapping a provider never touches code above llm/.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the llm package. Handles the only
    sub-package allowed to import a vendor LLM SDK or model-server HTTP client. Called by llm's
    public API on behalf of whatever calls llm itself; calls into sibling packages at Layer 1 or
    below, never back up into llm's other sub-packages directly.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under llm.
    - .claude/roadmap.md phase 3 for the work that first populates it.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
