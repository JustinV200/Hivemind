/**
 * The Cost reducer: take the read, then fold each completed call from the trail at once.
 *
 * The view reads `GET /v1/cost` when it opens, and the trail stream then carries every event the
 * Hive records. An `llm.call` with usage is folded into the matching goal, slot and provider as it
 * arrives (see `fold.ts`) and bumps `ticks`, which is the cue for the hook to read the cost again
 * once the burst settles; that read is the whole truth at one instant, so it replaces the figures,
 * and any call folded a moment before it is not counted twice. Frames of other kinds, and calls
 * without usage, change nothing and return the same state.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/slice/`. Wrapped as `costSlice` by `index.ts` and
 *   registered in `src/app.tsx`'s table.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O.
 *   - A reducer returns its input when an event changes nothing.
 *   - A call id already folded is skipped, so a frame replayed after a reconnect adds nothing.
 *
 * See Also:
 *   - src/views/cost/slice/fold.ts for the arithmetic.
 *   - docs/observation/design.md section 5.8.
 */
import { foldCall } from './fold';
import { initialCost, MAX_SEEN } from './state';
import type { CostEvent, CostState, TrailFrame } from './state';

function framed(state: CostState, frame: TrailFrame): CostState {
  const { event } = frame;
  if (state.seen.includes(event.id)) return state;
  const cost = foldCall(state.cost, event);
  // A frame that is not a counted call leaves the state alone, so nothing re-renders for it.
  if (cost === state.cost) return state;
  return {
    ...state,
    cost,
    ticks: state.ticks + 1,
    seen: [...state.seen, event.id].slice(-MAX_SEEN),
  };
}

export function reduceCost(state: CostState, event: CostEvent): CostState {
  switch (event.type) {
    case 'open':
      // Opening the view starts from nothing, so one visit's figures never mix with the next.
      return state === initialCost ? state : initialCost;
    case 'read':
      return { ...state, seeded: true, cost: event.cost, error: null };
    case 'failed':
      return { ...state, error: event.error };
    case 'frame':
      return framed(state, event.frame);
  }
}
