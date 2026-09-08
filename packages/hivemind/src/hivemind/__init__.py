"""Provide the Queen and every control-plane subsystem she depends on: the hivemind package.

The Queen is the Hive's central orchestrator; hivemind also holds memory, policy, the Cell
abstraction, the Hive Entrance, and the Observation Hive read side that she depends on. It is
organised into strict layers (section 4 of codingrules.md) so that lower-level subsystems, such
as the Cell abstraction, never know about higher-level ones, such as the Queen herself.

Fits into the Hive:
    The whole hivemind package spans Layers 0-7 of the layer table in codingrules.md section 4.
    It is called by nothing outside the workspace except the `hive` CLI entry point
    (packages/hivemind/src/hivemind/cli); it calls into waggle for envelopes, ids, the clock and
    the loop shape, and into nothing else outside the workspace.

Key invariants:
    - None yet: no code beyond this docstring exists, and `__all__` stays empty, until phase 0
      adds the first public name.

See Also:
    - .claude/codingrules.md section 4 for the full layer table this package implements.
    - hivemind.cli for the `hive` command line entry point.

Public API: none yet; first populated in phase 0.
"""

# Appendix A.3: nothing is re-exported yet; phase 0 adds the first public name.
__all__: list[str] = []
