# Supervision policy data

Data the Hive's supervisors (a human, the Queen, a Warden, or a sub-bee, all implementing the same
Supervisor protocol) load at runtime: `default-policy.toml` for escalation rules and
`capping-tiers.toml` for the Capping gate's risk tiers. First populated alongside the supervision
package in phase 3.

## `default-policy.toml`

Loaded by `hivemind.supervision.policy.load_policy` into an `EscalationPolicy`: a `default`
`PolicyAction` plus a list of `[[rules]]` tables, each an optional `kind` (an `AlarmKind` member
name; omitted means a wildcard row that matches any kind), a `min_attempts` (always >= 1) and an
`action`. `hivemind.supervision.policy.decide` picks, for an Alarm's own kind, the matching row
with the highest `min_attempts` the Alarm's `attempts` count clears; failing that, the same search
over the wildcard rows; failing that, `default`. The file's own header and per-rule comments record
why each threshold and action were chosen; `tests/unit/supervision/test_policy.py` loads it and
checks a table of `(kind, attempts) -> action` pairs, including the default case.

The manifest's `[supervision] policy_file` (roadmap step 3.1) names this file's path by default;
the Queen and each Warden load their own `EscalationPolicy` from it once at start-up.

## `capping-tiers.toml`

Not yet populated; lands with the Capping gate (roadmap step 3.17, dispatch C4).
