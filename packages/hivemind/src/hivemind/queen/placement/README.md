# hivemind.queen.placement

The placement package is the Queen's pure decision of where a task's Cell comes from: reuse an
already-attached Warden's own Real Cell, resume an Overwintered Virtual Cell, or provision a fresh
one (roadmap step 5.7, `docs/adr/0028-placement-policy-real-versus-virtual.md`). Roadmap step 6.12
adds the Exoskeleton to the fit rule (`docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md`).

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
- Exoskeleton fit (roadmap step 6.12, ADR-0031's "Needs travel with the task"): rule 4c asks of a
  Real Cell what attach will ask once it is leased, from the Cell's capability report and the
  widest Exoskeleton scopes its access level ever grants (`hivemind.guard.ceiling_for`, with the
  operator's running-display opt-in, exactly as the Cell's own Warden builds it):

  | Need | A Real Cell qualifies when |
  |---|---|
  | browser-only | `has_browser`, and the level grants `exoskeleton:browser` (SCRATCH or FULL) |
  | desktop | `can_start_display` and the level grants `exoskeleton:display` (FULL), or `has_display` and `real_display_allowed` and the level grants `exoskeleton:real_display` (FULL with the opt-in) |
  | audio (with desktop) | the desktop rule, plus `has_audio` and the level grants `exoskeleton:audio` (FULL) |

  `rules.exoskeleton_shortfall` returns the first unmet requirement in words (for example
  `desktop Exoskeleton needs a display, but cannot start one (access level SCRATCH never grants
  exoskeleton:display) or drive the running one (has_display=false)`), which becomes the Cell's
  line in the placement reason. `RealCandidate.access_level` carries the level and defaults to
  READ_ONLY, so a caller that does not report it fails closed. A Virtual spec fits any Exoskeleton
  need only when it provisions one (`VirtualCellSpec.exoskeleton`); the composition root offers
  one per backend, booting `[virtual_cells] exoskeleton_image` (`desktop-ubuntu` by default), after
  the terminal-only default spec, so a task without the need never boots the heavier image.
- `PlacementError`, `decide` (`decide.py`): `decide(needs, inventory, forage, policy) -> Placement`
  runs ADR-0028's own ordered pipeline -- Night Veil first (always a fresh Virtual Cell, gated by
  `check_night_veil` above before any backend is even considered), then isolation (`REQUIRED`
  excludes every Real Cell), then each side's own candidates are filtered (a `BLOCK` Cell Wax or
  `allow_hive_stand = false`, then fit -- OS, network scopes, the Exoskeleton fit above -- then
  Forage) and
  ranked (a `CAUTION` note behind a clean candidate, a dormant Cell before a fresh provision,
  attachment order breaking every other tie); only then is `prefer` read, and if the preferred
  side has nothing, the other side is used with a reason saying why. `PlacementError` names every
  rule that eliminated a candidate, on both sides, when neither has one. A backend the dispatcher
  holds back (`VirtualBackendCandidate.held_back`: its provisions keep failing, so it rests a
  while) is passed over like one with no headroom left, under the reason the dispatcher gave.
- The goal ceiling (roadmap step 10.3, ADR-0039) runs ahead of every rule above: with
  `ForageView.goal_capabilities` set, a candidate the goal does not allow is excluded with a reason
  naming what it lacked (`rules.placement_needs`, `virtual_placement_needs`, `goal_lacks`): the
  Hive Stand needs `cell:hive_stand`, any other Real Cell `cell:real:<cell id>`, a Virtual Cell
  `cell:virtual`, a Cell at a tier `cell:comb_shield:<tier>`. So `prefer = "real"` with a goal
  lacking `cell:hive_stand` reads `prefer=real found no Real Cell (Hive Stand: goal lacks
  cell:hive_stand); using Virtual instead.` When the ceiling alone leaves no candidate,
  `PlacementError.denied` names the missing capabilities; `decide` stays pure, and the dispatcher
  records one `guard.denied` for each. `RealCandidate.kind` lets a Virtual Cell attached through
  the listener ask `cell:virtual` (placement may read a Cell's kind, codingrules 8.7).

Never branches on `cell.kind`: `scripts/check_no_kind_branches.py` allowlists
`hivemind/queen/placement/` in full.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/queen/placement -q
```
