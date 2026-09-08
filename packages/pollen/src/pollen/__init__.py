"""Provide the Pollen Packet, the lightweight connector on a borrowed Real Cell device.

It depends only on waggle, so it can install on hardware with no Docker, no SQLite extensions
and no LLM SDK, such as a Raspberry Pi.

Fits into the Hive:
    Its own package, parallel to hivemind rather than inside its layer table; called by nothing
    in the workspace (it runs standalone on a borrowed device) and calls into waggle only, so it
    installs on hardware with no Docker, no SQLite extensions and no LLM SDK.

Key invariants:
    - None yet: no code beyond this docstring exists, and `__all__` stays empty, until phase 11
      adds the first public name.

See Also:
    - .claude/codingrules.md section 4 for the single-dependency rule this package follows.
    - waggle for the protocol and primitives this package links against.

Public API: none yet; first populated in phase 11.
"""

# Appendix A.3: nothing is re-exported yet; phase 11 adds the first public name.
__all__: list[str] = []
