"""Define the Transport protocol that carries signed Envelopes between two processes.

The implementations that satisfy it are an in-memory transport for tests and a WebSocket
transport for real Hives, each moving one signed Envelope (the outer wrapper every Waggle
message travels in) at a time. Higher layers depend only on the protocol, never on which
transport is in use.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Handles the Transport protocol and its in-memory and WebSocket implementations. Called by
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
