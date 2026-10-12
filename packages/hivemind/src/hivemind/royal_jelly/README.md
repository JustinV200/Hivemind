# hivemind.royal_jelly

The royal_jelly package is the Royal Jelly Lab and Comb Registry: how a Worker or Warden
requests a brand-new tool, has it scaffolded and quarantined, and, once approved, promoted into
the registry at hive or Cell scope. A tool is a package of files named by its SHA-256; the
registry stores the hash, never the bytes, and one hash names one immutable version everywhere
(ADR-0046, tool spec, Comb Registry and scopes).

## What exists

Phase 9's first wave (steps 9.1, 9.2 and 9.9) landed the spec, the registry, the state machine and
the errors. The Comb (`quarantine_comb/`), the scaffolder (`scaffold/`) and the promotion flow
(`promotion/`) are still docstring-only packages; later waves fill them.

- **Spec** (`spec/`): `ToolSpec` (name, version, model-facing description, argument schema,
  required capabilities, entry point `tool:run`, dependencies, supported OSes, scope, Cell) and
  `spec_sha256`, the one canonical hash of a spec. `validate_spec(spec, requester)` is pure and
  refuses a capability outside the requester's own set, a `tool:` capability, a schema that is not
  a closed object, a bad entry point and a scope that disagrees with its Cell. `pack`/`unpack`/
  `sha256_of` make and read the deterministic zip (sorted paths, fixed timestamp, fixed
  permissions, bounded size, no `..`, no absolute paths, no duplicates); `ToolPackage` is the same
  three functions as a namespace. `QuarantineReport` (with `GateResult`, `ResourceUsage`) is the
  Comb's verdict, validated so `passed` and `failing_gate` agree with the gates and a pass holds
  all six gates; `QuarantineLimits` is the budget a run gets.
- **Tool state** (`tool_state.py`): `ToolState` and the one `TRANSITIONS` table (Appendix C):
  `REQUESTED -> SCAFFOLDED -> QUARANTINING -> PROMOTED / REJECTED`, `PROMOTED -> RETIRED`.
- **Errors** (`errors.py`): `RoyalJellyError` and its subclasses, each with a stable `code`;
  `PromotionRefusedError` has six concrete reasons (`report_failed`, `report_mismatch`,
  `wrong_principal`, `wrong_cell`, `version_exists`, `needs_review`).
- **Registry** (`registry/`): the `CombRegistry` protocol (`list_tools`, `get`, `visible_tools`,
  `promote`, `retire`, `rollback`), `SqliteCombRegistry` over the `comb_tools` table (one row per
  name, scope, Cell and version; HIVE scope stores the Cell as the empty text so the unique index
  works), `InMemoryCombRegistry` (the fake), and the principals (`QueenPrincipal`,
  `WardenPrincipal`). `promote` is the only writer that creates a PROMOTED row and has no force
  flag. The registry records nothing on the Pheromone Trail: each write returns a `RegistryChange`
  and the caller records `tool.promoted`, `tool.rolled_back` or `tool.retired`.

## How a promotion is checked

```text
promote(spec, report, offer, principal, reason)
  principal: HIVE scope -> the Queen; CELL scope -> the Warden of spec.cell_id   (wrong_principal, wrong_cell)
  report.passed                                                                  (report_failed)
  report names this spec hash, package hash and size                             (report_mismatch)
  REAL_CELL-path report at HIVE scope -> offer.reviewed (the Queen's review)     (needs_review)
  (name, scope, Cell, version) not stored yet, in any state                      (version_exists)
  -> insert the row, clear the old current flag, all in one transaction
```

Retire and rollback use the same scope authority, except that the Queen may also retire or roll
back a CELL-scope tool (the operator's `hive tools` commands run through her). A rollback target
must be a PROMOTED, non-current, strictly older version whose own stored report still passes.

## How to test this

```text
uv run --frozen pytest -q -p no:cacheprovider packages/hivemind/tests/unit/royal_jelly \
    packages/hivemind/tests/contracts/test_comb_registry_contract.py
```

`tests/unit/royal_jelly/registry/test_no_bypass.py` is the no-bypass proof; the contract suite runs
the SQLite store (in memory and on a file) and the fake through the same clauses. Builders are in
`tests/builders/royal_jelly.py`.
