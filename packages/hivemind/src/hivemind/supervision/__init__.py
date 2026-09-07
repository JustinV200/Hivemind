"""Define the Supervisor protocol shared by every level of the Hive's chain of command.

The chain of command runs from a human through the Queen and a Warden to a sub-bee. This package
also provides Alarm, AlarmKind, ContextTelemetry and the Attendant (the inbox triage every
supervisor uses).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen and wardens, the two
    Supervisor implementations this phase adds. Calls into hivemind.common.

Key invariants:
    - None yet: this package holds no code beyond this docstring, and `__all__` stays empty,
      until phase 3 adds its first public name.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/roadmap.md phase 3 for the work that first populates this package.

Public API: none yet; first populated in phase 3.
"""

# Appendix A.3: nothing is re-exported yet; phase 3 adds the first public name.
__all__: list[str] = []
