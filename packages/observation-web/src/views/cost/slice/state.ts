/**
 * The Cost slice's types and its starting state.
 *
 * The slice holds the Hive's spend as the Observation Hive last saw it: the answer of `GET /v1/cost`
 * (per goal, slot and provider, with each slot's local saving) with every completed model call seen
 * on the trail since folded into it. Everything is the generated schema as received; the only shapes
 * declared here are the ones the view itself adds (the counter, the seen list, the error).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/slice/`. Imported by `fold.ts`, `reduce.ts` and,
 *   through the package face, by `use_cost.ts` and every table of the view.
 *
 * Key invariants:
 *   - A schema shape is never redeclared by hand; these are aliases of the generated types.
 *   - `seen` is bounded to `MAX_SEEN`, so a long session holds a fixed number of ids.
 *
 * See Also:
 *   - docs/observation/design.md section 5.8.
 */
import type { components } from '../../../landing_board/types';

type Schemas = components['schemas'];
export type CostSnapshot = Schemas['CostView'];
export type CostLine = Schemas['CostLine'];
export type GoalCost = Schemas['GoalCost'];
export type ProviderCost = Schemas['ProviderCost'];
export type SlotSaving = Schemas['SlotSaving'];
export type TrailFrame = Schemas['TrailFrame'];
export type TrailEventView = Schemas['TrailEventView'];

// Call ids remembered so a trail frame replayed after a reconnect is never counted twice.
export const MAX_SEEN = 500;

export interface CostState {
  // False until the first answer of `GET /v1/cost` lands; the view draws nothing before it.
  seeded: boolean;
  cost: CostSnapshot;
  // Counts the calls folded since the view opened; the hook reads the cost again when it grows.
  ticks: number;
  // Ids of the `llm.call` events already folded, newest last.
  seen: readonly string[];
  // The last failed read, shown through `ErrorNotice`; null when well.
  error: unknown;
}

export type CostEvent =
  | { type: 'open' }
  | { type: 'read'; cost: CostSnapshot }
  | { type: 'failed'; error: unknown }
  | { type: 'frame'; frame: TrailFrame };

// A Hive that has spent nothing: also what live calls fold into before the first read lands.
export const EMPTY_COST: CostSnapshot = {
  as_of: '',
  goals: [],
  if_local_saving_usd: 0,
  providers: [],
  slots: [],
  total_usd: 0,
};

export const initialCost: CostState = {
  seeded: false,
  cost: EMPTY_COST,
  ticks: 0,
  seen: [],
  error: null,
};
