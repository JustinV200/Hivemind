"""Hold the Guard's shipped data: the policy a Hive falls back to and the scanner's patterns.

`policy.toml` is the Guard policy (`hivemind.guard.policy`) every Hive starts from: each policy
role's default capability set, the hive-wide deny list and the escalation table (ADR-0039). A
Hive's manifest may overlay it with a `[guard]` table or replace it with `[guard] policy_file`,
which is the exception: policy is data an operator *may* override, not data every operator must
supply. `untrusted-content.toml` (roadmap step 10.6b, ADR-0043) is the untrusted-content
scanner's pattern families, one table each with its weight and seed examples; the manifest's
`[guard.untrusted_content]` sets the thresholds they are scored against, never the patterns.
Both are read through `importlib.resources.files("hivemind.guard.defaults")`, which is why this is
a real Python package (an `__init__.py`, however empty) rather than a bare directory:
`importlib.resources` addresses packages by dotted name, so the files ship inside the installed
distribution and resolve the same from a checkout, from a wheel and from a Hive directory that is
neither.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by
    `hivemind.guard.policy.defaults.load_guard_policy` when it is given no policy file, and by
    `hivemind.guard.scanner.patterns.load_scan_patterns`; nothing imports this module for a
    Python name.

Key invariants:
    - `policy.toml` is valid for `load_guard_policy`: it defines every policy role, only the
      `device` role has a `proposed` list, and every entry is a capability. The loader checks
      that at load time, not this module.
    - This is the default, never the only source: a manifest's `[guard]` table always overlays it.
    - `untrusted-content.toml` is valid for `load_scan_patterns`: every pattern compiles and has
      only bounded repetitions, and every family's examples fire it (a test holds it to that).

See Also:
    - hivemind.guard.policy.defaults for load_guard_policy, the policy's one reader.
    - hivemind.guard.scanner.patterns for load_scan_patterns, the patterns' one reader.
    - hivemind.supervision.defaults for the same pattern applied to the escalation policy.

Public API: none; this package holds data files (`policy.toml`, `untrusted-content.toml`), not
importable names.
"""

__all__: list[str] = []
