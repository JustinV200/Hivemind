/**
 * The Cost view's row models: the three tables' rows, built from the cost snapshot.
 *
 * The Entrance answers per goal (with a line per slot and provider), per slot (with its local
 * saving) and per provider. The tables also show calls and tokens, which only the lines carry, so
 * these builders add each slot's and provider's calls and tokens from the lines and split each
 * goal's spend by slot and by provider. The null goal becomes the row labelled "Unattributed".
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Used by the goal, slot and provider
 *   tables and the totals strip; the screen passes the slice's snapshot in.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O, the snapshot is never mutated.
 *   - Every row's `key` is unique within its table (the null goal's key cannot clash with an id).
 *   - A split lists the biggest spend first, ties by name, so the cell reads the same each time.
 *
 * See Also:
 *   - src/views/cost/slice/state.ts for the snapshot's types.
 */
import type { CostLine, CostSnapshot } from '../slice';

export const UNATTRIBUTED = 'Unattributed';

// One name and what it cost, in a goal's by-slot or by-provider cell.
export interface Split {
  name: string;
  usd: number;
}

export interface Tokens {
  input: number;
  output: number;
  cached: number;
}

export interface GoalRow {
  key: string;
  // The goal id, or null for the unattributed row.
  goal: string | null;
  spend: number;
  calls: number;
  tokens: Tokens;
  bySlot: readonly Split[];
  byProvider: readonly Split[];
}

export interface SlotRow {
  key: string;
  slot: string;
  spend: number;
  hosted: number;
  saving: number;
  calls: number;
  // Calls through a line that cost money. Hosted spend is by definition all of a slot's spend, so
  // the share that says something is the share of calls: how many a local model could have served.
  hostedCalls: number;
}

export interface ProviderRow {
  key: string;
  provider: string;
  calls: number;
  spend: number;
  tokens: Tokens;
}

const NO_TOKENS: Tokens = { input: 0, output: 0, cached: 0 };

function addTokens(tokens: Tokens, line: CostLine): Tokens {
  return {
    input: tokens.input + line.input_tokens,
    output: tokens.output + line.output_tokens,
    cached: tokens.cached + line.cached_tokens,
  };
}

// Sums `usd` per name over the lines, biggest first.
function splitBy(lines: readonly CostLine[], name: (line: CostLine) => string): Split[] {
  const sums = new Map<string, number>();
  for (const line of lines) sums.set(name(line), (sums.get(name(line)) ?? 0) + line.cost_usd);
  return [...sums]
    .map(([key, usd]) => ({ name: key, usd }))
    .sort((a, b) => b.usd - a.usd || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

export function goalRows(cost: CostSnapshot): GoalRow[] {
  return cost.goals.map((goal) => ({
    key: goal.goal_id === null ? '\u0000unattributed' : goal.goal_id,
    goal: goal.goal_id,
    spend: goal.cost_usd,
    calls: goal.calls,
    tokens: goal.lines.reduce(addTokens, NO_TOKENS),
    bySlot: splitBy(goal.lines, (line) => line.slot),
    byProvider: splitBy(goal.lines, (line) => line.provider),
  }));
}

// Calls through lines that cost money (a hosted provider) and all calls, over the given lines.
function callMix(lines: readonly CostLine[]): { hosted: number; all: number } {
  const sum = (items: readonly CostLine[]): number => items.reduce((n, l) => n + l.calls, 0);
  return { hosted: sum(lines.filter((l) => l.cost_usd > 0)), all: sum(lines) };
}

export const allLines = (cost: CostSnapshot): CostLine[] => cost.goals.flatMap((g) => g.lines);

// The whole Hive's calls, and how many of them were hosted.
export const hostedCalls = (cost: CostSnapshot): { hosted: number; all: number } =>
  callMix(allLines(cost));

export function slotRows(cost: CostSnapshot): SlotRow[] {
  const lines = allLines(cost);
  return cost.slots.map((slot) => {
    const mix = callMix(lines.filter((l) => l.slot === slot.slot));
    return {
      key: slot.slot,
      slot: slot.slot,
      spend: slot.cost_usd,
      hosted: slot.hosted_usd,
      saving: slot.if_local_saving_usd,
      calls: mix.all,
      hostedCalls: mix.hosted,
    };
  });
}

export function providerRows(cost: CostSnapshot): ProviderRow[] {
  const lines = allLines(cost);
  return cost.providers.map((provider) => ({
    key: provider.provider,
    provider: provider.provider,
    calls: provider.calls,
    spend: provider.cost_usd,
    tokens: lines.filter((l) => l.provider === provider.provider).reduce(addTokens, NO_TOKENS),
  }));
}

// Hosted spend over every slot: what the Hive paid a provider for, as against free local calls.
export const hostedTotal = (cost: CostSnapshot): number =>
  cost.slots.reduce((sum, slot) => sum + slot.hosted_usd, 0);
