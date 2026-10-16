/**
 * Folding one completed model call into the cost figures.
 *
 * An `llm.call` trail event carries its slot, its provider and its normalised usage. This module
 * adds that usage into the goal, slot and provider it belongs to, the same way the Entrance's
 * builder sums the trail, so a figure drawn from a frame and the same figure after the next read
 * agree. A call whose payload names no goal lands under the null goal (the "unattributed" row): the
 * browser cannot see which goal a grant was issued for, so it never guesses, and the read that
 * follows every folded call puts the call under its goal if the Entrance can attribute it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/slice/`. Called by `reduce.ts` for each trail frame.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O; the input snapshot is never mutated.
 *   - Only an `llm.call` with usage, a slot and a provider counts, as in the Entrance's builder.
 *   - Hosted spend is the spend of calls whose usage says the provider was not local (`local`
 *     false or absent): a local source priced by the seat-hour costs, yet that is amortised
 *     hardware, not money going local would save. A usage without the flag (an older payload, or
 *     a trail view that does not carry it yet) reads as not local, the rule that held before. The
 *     local saving of a slot is that hosted spend, an upper bound, as the Entrance defines it.
 *   - Lists keep the Entrance's order: goals costliest first with the null goal after its equals,
 *     providers costliest first, slots and lines by name.
 *
 * See Also:
 *   - packages/hivemind/src/hivemind/observation/api/cost.py for the builder this mirrors.
 */
import type {
  CostLine,
  CostSnapshot,
  GoalCost,
  ProviderCost,
  SlotSaving,
  TrailEventView,
} from './state';

export const CALL_KIND = 'llm.call';

const compareText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

// The goal a call names itself; a string only, as the Entrance reads it (grants are not visible here).
function goalOf(event: TrailEventView): string | null {
  const goal = event.payload['goal_id'];
  return typeof goal === 'string' ? goal : null;
}

// Whether the call's provider was inside the Hive. The flag is read structurally, since the
// generated trail usage type may not carry it yet; absent, the call is hosted (the older rule).
function isLocal(usage: TrailEventView['usage']): boolean {
  const flag: unknown = usage && 'local' in usage ? usage.local : undefined;
  return flag === true;
}

// Replaces the first item `same` accepts with `update(it)`, or appends `update(made)` when none is.
function upsert<T>(
  items: readonly T[],
  same: (item: T) => boolean,
  made: T,
  update: (item: T) => T,
): T[] {
  const found = items.some(same);
  return found ? items.map((item) => (same(item) ? update(item) : item)) : [...items, update(made)];
}

const byGoal = (a: GoalCost, b: GoalCost): number =>
  b.cost_usd - a.cost_usd ||
  Number(a.goal_id === null) - Number(b.goal_id === null) ||
  compareText(a.goal_id ?? '', b.goal_id ?? '');

const byLine = (a: CostLine, b: CostLine): number =>
  compareText(a.slot, b.slot) || compareText(a.provider, b.provider);

function foldGoal(goal: GoalCost, line: CostLine): GoalCost {
  const same = (l: CostLine): boolean => l.slot === line.slot && l.provider === line.provider;
  // A line seen for the first time starts from nothing, so the call is added once, not twice.
  const blank: CostLine = {
    ...line,
    calls: 0,
    cost_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
  };
  const lines = upsert(goal.lines, same, blank, (l) => ({
    ...l,
    calls: l.calls + line.calls,
    cost_usd: l.cost_usd + line.cost_usd,
    input_tokens: l.input_tokens + line.input_tokens,
    output_tokens: l.output_tokens + line.output_tokens,
    cached_tokens: l.cached_tokens + line.cached_tokens,
  }));
  return {
    ...goal,
    calls: goal.calls + line.calls,
    cost_usd: goal.cost_usd + line.cost_usd,
    lines: lines.sort(byLine),
  };
}

function foldSlot(slot: SlotSaving, usd: number, local: boolean): SlotSaving {
  // A local call's cost is amortised hardware: spend, yet nothing going local would save.
  const hosted = local ? 0 : usd;
  return {
    ...slot,
    cost_usd: slot.cost_usd + usd,
    hosted_usd: slot.hosted_usd + hosted,
    if_local_saving_usd: slot.if_local_saving_usd + hosted,
  };
}

// The call's usage as the one-call line it adds; null when the event is not a counted call.
function lineOf(event: TrailEventView): CostLine | null {
  const { usage, slot, provider } = event;
  if (event.kind !== CALL_KIND || !usage || !slot || !provider) return null;
  return {
    goal_id: goalOf(event),
    slot,
    provider,
    calls: 1,
    cost_usd: usage.cost_usd,
    input_tokens: usage.input_tokens,
    output_tokens: usage.output_tokens,
    cached_tokens: usage.cached_tokens,
  };
}

// Adds a call into the snapshot; the same snapshot comes back when the event is not a counted call.
export function foldCall(cost: CostSnapshot, event: TrailEventView): CostSnapshot {
  const line = lineOf(event);
  if (!line) return cost;
  const local = isLocal(event.usage);
  const goals = upsert(
    cost.goals,
    (g) => g.goal_id === line.goal_id,
    { goal_id: line.goal_id, calls: 0, cost_usd: 0, lines: [] },
    (g) => foldGoal(g, line),
  );
  const slots = upsert(
    cost.slots,
    (s) => s.slot === line.slot,
    { slot: line.slot, cost_usd: 0, hosted_usd: 0, if_local_saving_usd: 0 },
    (s) => foldSlot(s, line.cost_usd, local),
  );
  const providers = upsert<ProviderCost>(
    cost.providers,
    (p) => p.provider === line.provider,
    { provider: line.provider, calls: 0, cost_usd: 0 },
    (p) => ({ ...p, calls: p.calls + 1, cost_usd: p.cost_usd + line.cost_usd }),
  );
  return {
    ...cost,
    total_usd: cost.total_usd + line.cost_usd,
    if_local_saving_usd: cost.if_local_saving_usd + (local ? 0 : line.cost_usd),
    goals: goals.sort(byGoal),
    slots: slots.sort((a, b) => compareText(a.slot, b.slot)),
    providers: providers.sort(
      (a, b) => b.cost_usd - a.cost_usd || compareText(a.provider, b.provider),
    ),
  };
}
