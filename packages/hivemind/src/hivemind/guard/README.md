# hivemind.guard

The guard package is the Hive's policy engine: `CapabilitySet` (what one bee is currently allowed
to do) and `access.py` (what each `AccessLevel` permits). The security tier enums themselves live
in `cell/tiers.py`; guard only interprets them. This package is pure: no I/O.

## Public API (roadmap step 3.13a)

- **Capabilities** (`hivemind.guard.capabilities`): `CapabilityFamily` (`TOOL`, `FS_READ`,
  `FS_WRITE`, `NET`, `EXEC`, `DEVICE`, `SPEND`), the seven families this step builds -- phase 10
  step 10.1 adds the rest (Entrance, Honey, Exoskeleton scopes). `Capability`, a frozen
  `family` + `scope` pair with a `"family:scope"` string form (`Capability.parse` /
  `str(capability)` round-trip) and `matches(needed)`: glob-aware (`fnmatch.fnmatchcase` on
  POSIX-style paths, so Windows backslash paths still match) for `tool`/`fs:read`/`fs:write`/
  `exec`; exact-or-trailing-`*` for `net`/`device`; numeric `held >= needed` for `spend`.
  `CapabilitySet`, the frozenset of every `Capability` one bee holds, with `allows(needed)`,
  `attenuate(subset)` (raises `CapabilityWideningError` rather than ever widening),
  `issubset(other)`, `CapabilitySet.parse(*strings)`, `CapabilitySet.empty()`, `len()` and
  iteration. There is no `union`: nothing in this package can make a set wider.
- **Access** (`hivemind.guard.access`): `ceiling_for(level, scratch_root)` builds the widest
  `CapabilitySet` an `AccessLevel` (`READ_ONLY`, `SCRATCH`, `FULL`, `hivemind.cell.tiers`) ever
  permits on one Cell; `cap_to_access(requested, level, scratch_root)` narrows a requested set
  down to what that ceiling allows, so a `READ_ONLY` device can never receive a write capability
  however `requested` is built.
- **Errors** (`hivemind.guard.errors`): `GuardError` (root), `InvalidCapabilityError` (a
  capability string does not parse), `CapabilityWideningError` (`attenuate` was asked for
  something wider than the attenuating set allows).

Phase 10 step 10.1 extends `CapabilityFamily` with the remaining families (`entrance:*`,
`honey:*`, `exoskeleton`, ...) and step 10.2 adds the `[guard]` manifest section and the policy
engine (`evaluate`); step 10.3 wires enforcement points. None of that exists yet.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/guard
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.guard uv run --frozen pytest -p no:cacheprovider --cov=hivemind.guard \
    --cov-report=term-missing packages/hivemind/tests/unit/guard
```
