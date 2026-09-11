"""Hold the escalation policy and Capping tier table a Hive falls back to; carries no code.

`default-policy.toml` is the `EscalationPolicy` (`hivemind.supervision.policy`) and
`capping-tiers.toml` the Capping risk-tier table (`hivemind.supervision.capping.tiers`) that a
Hive uses when its manifest's `[supervision] policy_file` / `capping_tiers_file` are unset, which
is the normal case: policy is data an operator *may* override, not data every operator must supply.
Both are read through `importlib.resources.files("hivemind.supervision.defaults")`, which is why
this needs to be a real Python package (an `__init__.py`, however empty) rather than a bare
directory, and why these files live here rather than under `docs/`: `importlib.resources` addresses
packages by dotted name, not by filesystem path, so they ship inside the installed distribution and
resolve identically from a checkout, from a wheel, and from a Hive directory that is neither.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by
    `hivemind.supervision.policy.load_policy` and `hivemind.supervision.capping.tiers.load_tiers`
    when each is called with no path; nothing imports this module for a Python name.

Key invariants:
    - Every file here is valid TOML for its own model: `EscalationPolicy` for
      `default-policy.toml`, `TierTable` for `capping-tiers.toml`. The two loaders validate that
      at load time, not this module.
    - These are defaults, never the only source: a manifest that names its own file gets that file
      instead, resolved against the manifest's own directory.

See Also:
    - hivemind.supervision.policy for load_policy, one of this package's two readers.
    - hivemind.supervision.capping.tiers for load_tiers, the other.
    - docs/supervision/README.md for the operator-facing description of both tables.
    - hivemind.brood_chamber.store.migrations for the other data-only package on this pattern.

Public API: none; this package holds data files (`.toml` tables), not importable names.
"""

__all__: list[str] = []
