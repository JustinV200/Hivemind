# hivemind.cell

The cell package defines the Cell abstraction shared by every kind of machine the Hive runs work
on: Cell, CellKind (REAL for a borrowed device or VIRTUAL for a provisioned one), CellSession (a
terminal session on it), leases, TaskNeeds and the three security tier enums (AccessLevel,
CombShieldLevel, HoneyClearance). It knows what a Cell is, never how one is made.

## Public API (roadmap step 2.3a)

- **Tiers** (`hivemind.cell.tiers`): `AccessLevel` (`READ_ONLY`, `SCRATCH`, `FULL`, with a
  `rank` property), `CombShieldLevel` (`MEADOW`, `PROPOLIS`, `NIGHT_VEIL`) and `HoneyClearance`
  (`C0`, `C1`, `C2`, with a `rank` property). Member names and values mirror
  `waggle.messages.labels`'s enums of the same names; each also offers `from_wire`/`to_wire`.
- **Needs** (`hivemind.cell.needs`): `Isolation` (`REQUIRED`, `PREFERRED`, `NONE`), `OsFamily`
  (mirrors `waggle.messages.OsFamily`) and `TaskNeeds`, the frozen pydantic model a `TaskSpec`
  will carry: isolation, whether the task needs an Exoskeleton, its required OS, its network
  scopes, whether its Cell is disposable, the minimum `CombShieldLevel` and its `Tempo`. A
  `comb_shield == NIGHT_VEIL` task always requires `isolation == REQUIRED`.

Cell, CellKind, CellSession and leases are not implemented yet; they land in phase 3.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/cell
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.cell uv run --frozen pytest -p no:cacheprovider --cov=hivemind.cell \
    --cov-report=term-missing packages/hivemind/tests/unit/cell
```
