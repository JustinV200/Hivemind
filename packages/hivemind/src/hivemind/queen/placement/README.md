# hivemind.queen.placement

The placement package is the Queen's pure decision of where a task's Cell comes from: reuse an
already-attached Warden's own Real Cell, resume an Overwintered Virtual Cell, or provision a fresh
one (roadmap step 5.7, `docs/adr/0028-placement-policy-real-versus-virtual.md`).

## Public API (roadmap step 5.7)

- `Placement` (`models.py`): a union of `ReuseReal(cell_id, warden_id, reason)`,
  `ReuseDormant(cell_id, warden_id, reason)` and `ProvisionVirtual(spec, backend, reason)`. Every
  variant carries its own `reason`, including any Cell Wax that weighed on the decision.
- `Inventory`, `RealCandidate`, `VirtualBackendCandidate`, `DormantCandidate`, `WaxMention`,
  `ForageView` (`inventory.py`): the pure snapshot `decide` reads, precomputed by its caller
  (`hivemind.queen.dispatcher`) -- no I/O happens inside this package.
- `PlacementPolicy`, `VirtualSpecTemplate`, `Prefer` (`policy.py`): the `[placement]`/
  `[virtual_cells]`-derived value object `decide` reads: `prefer`, `allow_hive_stand`, per-role
  overrides, and the manifest's own default Virtual spec template.
- `NightVeilConstraints`, `NightVeilHostingView`, `check_night_veil` (`policy.py`, roadmap step
  5.7a): the `[security]` Night Veil tier profile (`PlacementPolicy.night_veil`) and the pure check
  `decide`'s NIGHT_VEIL branch runs beyond ADR-0028's rule 2 -- human-originated request, a
  configured VPN_TOR profile, every model slot resolving locally -- returning one violation string
  per broken rule. `ForageView` (`inventory.py`) carries the two facts this needs beyond
  `TaskNeeds` (`request_origin`, `night_veil_hosting`), both defaulted so every pre-5.7a caller is
  unaffected; see that dataclass's own docstring for why the real wiring of both is a report item.
- `rules` (`rules.py`): one small, individually-tested function per ADR-0028 rule.
- `PlacementError`, `decide` (`decide.py`): `decide(needs, inventory, forage, policy) -> Placement`
  runs ADR-0028's own ordered pipeline -- Night Veil first (always a fresh Virtual Cell, gated by
  `check_night_veil` above before any backend is even considered), then isolation (`REQUIRED`
  excludes every Real Cell), then each side's own candidates are filtered (a `BLOCK` Cell Wax or
  `allow_hive_stand = false`, then fit -- OS, network scopes, Exoskeleton -- then Forage) and
  ranked (a `CAUTION` note behind a clean candidate, a dormant Cell before a fresh provision,
  attachment order breaking every other tie); only then is `prefer` read, and if the preferred
  side has nothing, the other side is used with a reason saying why. `PlacementError` names every
  rule that eliminated a candidate, on both sides, when neither has one.

Never branches on `cell.kind`: `scripts/check_no_kind_branches.py` allowlists
`hivemind/queen/placement/` in full.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/placement -q
```
