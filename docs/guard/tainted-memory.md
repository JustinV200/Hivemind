# Tainted memory

Roadmap step 10.6d, [ADR-0035](../adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md).
Code: `hivemind.memory.taint`. State machine: [codingrules Appendix C](../../.claude/codingrules.md),
"Taint label" row.

A bee that read an injected instruction may have written it into its memory: a Handoff it will
resume from, an episode record of a decision, a checkpoint's transcript, a Nectar deposit. Deleting
that memory would lose the evidence, and a false positive would destroy good work. So it is
labelled instead. A tainted item is refused outright by everything that puts memory in front of a
model, whatever its relevance, until an independent judge clears it.

## The label

`TaintMarker` is one optional field, `tainted`, on every item that can carry it:

| Kind (`TaintedKind`) | Where it lives today |
|---|---|
| `handoff` | `memory.Handoff`, the `memory_handoffs` table |
| `episode` | `memory.EpisodeRecord`, the `memory_episodes` table |
| `bee_bread` | `memory.BeeBreadEntry` (a checkpoint's transcript, a tool result, a Handoff's index entry), the `memory_bee_bread` table |
| `nectar`, `honey` | declared for phase 7 (roadmap 7.4, 7.7): the Honey Store implements the same ledger |

The marker carries its state (`tainted` or `cleared`), its closed `TaintSource`, a bounded reason,
the id of the `memory.tainted` event that set it and when, and, once cleared, the id and time of the
`memory.taint_cleared` event. Migration `0004_add_taint_label.sql` adds the columns to the three
memory tables; an item written before it is unlabelled.

## Who sets it

`taint_memory(scope, stamp, ctx, extra_ledgers=())` is the only function that writes a `tainted`
label. Three callers may call it, one per `TaintSource`:

| Source | Caller | Roadmap |
|---|---|---|
| `isolation` | the Queen isolating a Cell, from the Guard report's first cited event on ([isolation](isolation.md)); and, on her `CellTaintOrder`, the Cell's own Warden over the store it keeps inside the Cell | 10.6a |
| `quarantine` | the one quarantine intervention in `wardens/`, from the suspect episode on | 10.6c |
| `guard_report` | the Queen acting on a Guard report about a Honey item | 10.6, phase 7 |

`tests/unit/memory/taint/test_only_setter.py` parses every module under `hivemind/` and fails when
a label is written anywhere else: a `write_taint(...)` call or a `TaintMarker(...)` built outside
`taint/set.py` and `taint/clear.py`, a `"tainted"` key set outside the stores that persist the
label, a `tainted=` argument that is not a label read off another item, or a call to
`taint_memory` from a module that is not one of the three setters. Each setter's module is in the
test's `_SETTER_CALLERS`, bound to its own source. 10.6a and 10.6c have landed, so the test also
pins each as real: `wardens/quarantine/path.py` is the only module that labels with `quarantine`;
exactly two modules label with `isolation`, `queen/isolation/taint.py` on the Hive's tables and
`wardens/isolation/taint.py`, the in-Cell setter call a Warden makes on its own store at the Queen's
order (a Virtual Cell keeps its store inside the Cell, where her label cannot reach); and nothing
else in `wardens/` calls the setter.

A `TaintScope` names the slice of memory one taint covers: the bees whose items it covers (a
Handoff's `written_by`, an episode's principal), the tasks whose items it covers, the moment from
which items count (inclusive), and which kinds of item it reaches.

- `TaintScope.for_bee(bee_id, since_id, task_ids=())` is quarantine's shape: every item of one bee
  and its tasks from the suspect episode on.
- `TaintScope.for_cell(authors, task_ids, since_id)` is isolation's shape. The memory tables carry
  no Cell id, so the Queen names the Cell's bees and tasks, which she knows from the Brood Chamber
  and the trail.

`scope.covers(...)` is the one predicate every ledger applies, so both memory stores (and the Honey
Store later) always agree on what a scope reaches. For each covered item that is not already
tainted, `taint_memory` records a `memory.tainted` event and writes the marker carrying that
event's id, in one transaction. An item already tainted keeps its first marker. A cleared item can
be tainted again by a later incident.

`memory.tainted` payload: `item_kind`, `source`, `reason` and, when the stamp names one,
`cause_event_id` (the report or episode that justified it). Never any of the item's content.

## Who refuses it

| Reader | What happens to a tainted item |
|---|---|
| `memory.assemble` | A tainted decision, a tainted retrieved item (`AssembleRequest.retrieved`) and a tainted resumed Handoff are left out and listed in `Prompt.refused`; none is handed to `on_drop`. A resumed Handoff above the reader's clearance is refused the same way; it used to bypass the clearance filter. |
| `memory.read_handoff` and every Handoff loader | Raises `TaintedMemoryError`. |
| A Warden's resume gate | A `TaskAssign` that resumes from a Handoff the Warden's own store labels tainted is refused before anything spawns: `guard.denied` at the `isolation` point (`guard.scope.tainted_handoff`), the task's grant withdrawn, the task reported held (`wardens/isolation/gate.py`). A quarantined task's resume meets the quarantine's own gate first. |
| The Worker runtime | A `TaskAssign` that resumes from a tainted Handoff fails the attempt with a `WORKER_CRASHED` Alarm before the role runs, so the Warden's escalation policy decides what follows. |
| The memory stores | Every list a prompt is built from (episodes, Bee Bread by task or by time) leaves a `tainted` row out (`taint_state IS NOT 'tainted'` in SQLite), and a Bee Bread lookup by id raises `TaintedMemoryError`. A Handoff lookup returns the Handoff with its marker, for its loader to refuse. A `cleared` row is ordinary memory again. |
| Phase 7 retrieval | Builds `RetrievedItem`s carrying the label, and `assemble` refuses the tainted ones. |

## Who clears it

Only a judge verdict clears it. `clear_taint(TaintClearRequest(target, clearer, held),
TaintClearDeps(judge, enforcer, ctx, ledger=None))` is the one path:

1. It reads the item and refuses unless it is tainted.
2. It asks the Guard at the `taint_clear` enforcement point (ADR-0031). The clearer (the Queen, or
   a Warden for its own sub-bee's respawn) must hold `honey:clearance:<c>` at the item's own
   clearance, because clearing sends the item's whole text to the judge. A refusal is a
   `guard.denied` row, and no model is called.
3. It refuses to review an item longer than 16,000 characters (`MAX_REVIEW_CHARS`). A verdict on
   an item's head must never clear its tail.
4. It asks the judge (`ModelTaintJudge`, on the `JUDGE` slot) with a bounded wait of 300 seconds.
   The judge sees only the item's kind, who tainted it and why, and its whole text, fenced as
   retrieved content. It sees nothing of the bee that wrote it. The rubric is the prompt
   `hivemind.llm.prompts` ships as `taint_review.md` (rubric id `taint.v1`). The judge clears an
   item only if it holds no instruction to a model, asks for nothing its own task does not need,
   carries nothing shaped like a tool call, a role marker or an unexplained blob, and would do only
   what its own goal describes as a plan. Otherwise, or when unsure, it keeps it.
5. Only `CLEAR` changes anything: the `cleared` marker and its `memory.taint_cleared` event are
   written in one transaction.

| Outcome | Meaning |
|---|---|
| `cleared` | The judge returned `CLEAR`; the item is usable again. |
| `kept` | The judge returned `KEEP`; nothing changed. |
| `refused` | The `taint_clear` point refused the clearer; no judge was asked. |
| `unreviewable` | Too long to show whole, or the judge could not answer in time. Nothing changed. |

Every path but `cleared` leaves the item exactly as it was: clearing fails closed.
`memory.taint_cleared` payload: `item_kind`, `rubric_id`, `tainted_by` (the event that set the
label) and `source`. Never the item's content or the judge's reasons; the caller gets those.

After a quarantine (10.6c), the only way back is a respawn from a Handoff the judge has cleared.
The quarantine writes that Handoff first (its checkpoint), then taints it with the rest of the
bee's memory from the suspect episode on. The Warden's gate (`wardens/quarantine/gate.py`)
refuses every respawn of the held task at the `quarantine` enforcement point until the Queen's
resume names that checkpoint and `read_handoff` reads it as `cleared`; a clearer asks the judge
through `clear_taint` like any other. `tests/e2e/test_quarantine_on_hive_stand.py` runs the whole
cycle on a real Hive Stand run: quarantined mid-command, refused while tainted, let out once
cleared.

## Phase 7 seams

- `TaintLedger` is the store seam (`find_taintable`, `read_taintable`, `write_taint`). Both memory
  stores satisfy it. The Honey Store will too: it is passed to `taint_memory` in
  `extra_ledgers`, and to `clear_taint` as `TaintClearDeps.ledger` for an item that lives there.
  `tests/builders/taint.py` holds `SeamLedger`, the test stand-in that proves the seam carries a
  Nectar deposit and a Honey hit end to end.
- `TaintedNectarRipener` declares the House Bee duty: re-ripen a tainted Nectar deposit with the
  flagged span stripped into a new Honey item that starts tainted (through `taint_memory`) and goes
  to the judge. The old deposit is retired, never edited. Nothing implements it until the Honey
  Store exists.
- `TaintSource.GUARD_REPORT` is the Queen's source for a Guard report about a Honey item.

## Tests

- `tests/unit/memory/taint/test_taint_reaches_no_prompt.py` is roadmap 10.6d's own test. It seeds
  a tainted Handoff (with its Bee Bread index), an episode, a Nectar deposit and a Honey hit, and
  asserts none reaches a prompt until one judge verdict clears the Handoff, and only the Handoff.
- `tests/contracts/test_memory_store_taint_contract.py` runs the taint half of the store contract
  over both memory stores.
- `tests/unit/memory/taint/test_only_setter.py` is the only-setter walk above.
- `tests/unit/llm/prompts/snapshots/taint_review.txt` pins the judge's rubric prompt.
