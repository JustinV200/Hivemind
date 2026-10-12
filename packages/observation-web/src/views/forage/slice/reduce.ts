/**
 * The Forage reducer: seed from the ledger reads, then fold each stream frame at once.
 *
 * The view reads `GET /v1/forage`, `/v1/wardens` and `/v1/llm` together when it opens, and the
 * forage stream then carries one `ForageFrame` per ledger event: the event, the grant it names as
 * the ledger holds it now, and the shared pool's headroom after it. A frame upserts its grant,
 * replaces the headroom and, for a revoked or expired grant, drops it. The Royal Reserve and the
 * capacity reports are not on a frame, so they stay as read until a `forage.capacity_reported`
 * frame ticks `capacityTicks` and the hook reads the ledger again (a reaction to a frame, never a
 * timer). A frame that beats the seed home wins over it, and a grant it removed stays removed.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/slice/`. Wrapped as `forageSlice` by `index.ts`
 *   and registered in `src/app.tsx`'s table.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O; times come in with the events.
 *   - A reducer returns its input when an event changes nothing.
 *   - Grants stay in the ledger's order (holder, then id) and each id appears once.
 *
 * See Also:
 *   - src/views/forage/slice/requests.ts for the request list.
 *   - docs/observation/design.md section 5.3.
 */
import { foldRequests } from './requests';
import { initialForage, MAX_GONE } from './state';
import type { ForageEvent, ForageFrame, ForageSnapshot, ForageState, GrantView } from './state';

const compareText = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

// The ledger's own order, so the table and the diagram read the same on every render.
export const byHolderThenId = (a: GrantView, b: GrantView): number =>
  compareText(a.holder, b.holder) || compareText(a.id, b.id);

// Replaces the grant with the same id, or adds it, keeping the order.
function upsert(grants: readonly GrantView[], grant: GrantView): readonly GrantView[] {
  return [...grants.filter((g) => g.id !== grant.id), grant].sort(byHolderThenId);
}

const REMOVING = new Set(['forage.revoked', 'forage.expired']);

function remember(gone: readonly string[], id: string): readonly string[] {
  return gone.includes(id) ? gone : [...gone, id].slice(-MAX_GONE);
}

function framed(state: ForageState, frame: ForageFrame): ForageState {
  const { event } = frame;
  const removes = REMOVING.has(event.kind);
  // A revoked or expired grant leaves the ledger, so the frame names it as null: drop our copy.
  let grants = state.grants;
  let gone = state.gone;
  if (removes) {
    grants = grants.filter((g) => g.id !== event.subject_id);
    gone = remember(gone, event.subject_id);
  } else if (frame.grant) {
    grants = upsert(grants, frame.grant);
    gone = gone.filter((id) => id !== event.subject_id);
  }
  return {
    ...state,
    grants,
    gone,
    headroom: frame.headroom,
    requests: foldRequests(state.requests, event),
    capacityTicks: state.capacityTicks + (event.kind === 'forage.capacity_reported' ? 1 : 0),
    framed: true,
  };
}

function seeded(state: ForageState, event: Extract<ForageEvent, { type: 'seeded' }>): ForageState {
  const { forage } = event;
  // Frames that arrived before the answer are the newer word on their grants and the headroom.
  const held = state.framed ? state.grants : [];
  const gone = new Set(state.gone);
  const read = forage.grants.filter((g) => !gone.has(g.id) && !held.some((h) => h.id === g.id));
  return {
    ...state,
    seeded: true,
    capacities: forage.capacities,
    reserve: forage.reserve,
    grants: [...held, ...read].sort(byHolderThenId),
    headroom: state.framed && state.headroom ? state.headroom : forage.headroom,
    llm: event.llm,
    wardens: event.wardens.wardens,
    error: null,
  };
}

// A re-read after a capacity report is the whole ledger at one instant, so it replaces it.
function reseeded(state: ForageState, forage: ForageSnapshot): ForageState {
  return {
    ...state,
    capacities: forage.capacities,
    reserve: forage.reserve,
    grants: [...forage.grants].sort(byHolderThenId),
    headroom: forage.headroom,
    error: null,
  };
}

export function reduceForage(state: ForageState, event: ForageEvent): ForageState {
  switch (event.type) {
    case 'open':
      // Opening the view starts from nothing, so a visit's old grants never mix with the new seed.
      return state === initialForage ? state : initialForage;
    case 'seeded':
      return seeded(state, event);
    case 'reseeded':
      return reseeded(state, event.forage);
    case 'failed':
      return { ...state, error: event.error };
    case 'frame':
      return framed(state, event.frame);
  }
}
