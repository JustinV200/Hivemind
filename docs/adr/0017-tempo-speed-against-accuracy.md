# ADR-0017: One table decides every grade floor a task's tempo implies

- Status: Accepted
- Date: 2026-09-08

## Context

Every task carries a tempo (`hivemind.forage.tempo.Tempo`): how fast it must finish, and how right
it must be. Several independent layers built across phase 3 each need to read the accuracy half of
that setting and derive their own floor from it — `llm.routing` picks a minimum model grade a bound
source must clear, `forage.allocate.grant` filters which Forage map sources a grant may name, and
Capping (the quality gate) lengthens or, within limits, shortens a proposal's check ladder — but
none of these call sites should be free to invent its own numbers independently, because two paths
disagreeing on "what grade counts as HIGH" would make Hive behaviour depend on which code happened
to run first. Roadmap step 3.12 names the concrete function this dispatch owns:
`forage/tempo.py`'s `grade_floor(bar: AccuracyBar) -> int`, mapping `LOW` to 1, `NORMAL` to 2,
`HIGH` to 3 and `CRITICAL` to 4, sitting beside the `Tempo`/`AccuracyBar` types phase 2 already
built.

## Decision

`grade_floor` is one pure function backed by exactly one table, `GRADE_FLOORS: Mapping[AccuracyBar,
int]`, defined in `forage/tempo.py` beside `Tempo` and `AccuracyBar` themselves — not in
`forage/allocate.py`, not in `llm/routing.py` — so every reader of a task's tempo reaches for the
same authoritative floor rather than a local copy. The four floors are hand-set, each with its own
stated reason rather than a shared blanket comment: `LOW` accepts the Forage map's weakest usable
grade (1) because an urgent, low-stakes task should never wait behind a strong model's queue for
marginal accuracy it does not need; `NORMAL` (2) is the ordinary floor most work already clears
without asking for anything special; `HIGH` (3) buys the upper half of the map's 1-to-5 scale for
work worth the extra cost; `CRITICAL` (4), deliberately not 5, leaves the top grade as headroom
above the floor rather than consuming it, because codingrules section 8.10 already reserves the
single strongest available source for the Queen's own reasoning regardless of any task's tempo — if
`CRITICAL`'s floor were 5, an ordinary critical task and the Queen's own awake episode would compete
for identically graded sources, collapsing a distinction the Hive needs to keep. Grade floors rise
strictly with accuracy (tested directly), and the table is total over every `AccuracyBar` member
(also tested), so no caller has to guard against an unmapped bar. `forage.allocate.grant` is this
phase's first, and only, caller: it keeps a Forage map source in a grant's `allowed` set exactly
when `source.spec.grade >= grade_floor(tempo.accuracy)`, so the allocator and any later
`llm.routing` decision can never disagree about which sources clear a given task's bar.

## Consequences

Positive: adding a new accuracy tier later — an `ULTRA` above `CRITICAL`, say — is a one-line table
edit plus one test-fixture update, not a search through every call site that might have hardcoded a
number. Because `grade_floor` is total and its range is checked against the Forage map's own grade
bounds (`MIN_MODEL_GRADE`/`MAX_MODEL_GRADE`, `waggle.messages.forage.values`), no caller anywhere in
the Hive can receive an out-of-range or missing floor, eliminating a whole class of "what does this
return for a value I forgot to handle" bugs at every one of `grade_floor`'s eventual call sites.

Negative: the floors are hand-set constants with no adaptive or measured component of their own —
if a later phase's evaluation harness finds that grade-3 sources routinely fail `HIGH`-tempo tasks
in practice, raising the floor is a manual code change and a new PR, not something the system tunes
for itself. This tradeoff is accepted deliberately here, because codingrules section 8.10 already
places the adaptive part of this story one level down, on the grade itself ("grades start as
hand-set values and are replaced by measured grade from the Hive's own evaluation runs"): the floor
stays a fixed policy choice, and the thing it is compared against is what gets to move.

## Alternatives considered

Making `grade_floor` a method on `Tempo` itself (`tempo.grade_floor`) instead of a free function
keyed by `AccuracyBar`: would tie the lookup to a full `Tempo` instance a caller might not have on
hand when only the accuracy bar matters, and the wire `AccuracyBar` mirror this module already
maintains for `waggle.messages.labels.AccuracyBar` would need the identical logic duplicated or
imported across a package boundary for no benefit.

Letting each of `llm.routing`, `forage.allocate` and Capping define its own floor mapping, since
each reads tempo for a different purpose: would let the three drift out of sync silently over time,
exactly the failure mode codingrules 8.10's "each role has a grade floor the Queen never goes
below" is written to prevent — a single shared table is the only way to guarantee agreement by
construction rather than by convention.

Computing the floor arithmetically from the enum's declaration order (e.g. `bar.index_in_enum() +
1`) instead of an explicit table: shorter to write today, but it would silently tie every grade
floor in the Hive to `AccuracyBar`'s member ordering, so an otherwise harmless refactor — reordering
the enum's members, or inserting a new one in the middle — would quietly change floors nobody
intended to touch. An explicit table makes every floor a value someone chose on purpose, visible in
one place, immune to reordering elsewhere in the file.
