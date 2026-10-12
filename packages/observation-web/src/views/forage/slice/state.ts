/**
 * The Forage slice's types and its starting state.
 *
 * The slice holds the Queen's Forage ledger as the Observation Hive last saw it: every Cell's
 * capacity report, every live grant, the shared pool's headroom and the Royal Reserve (the seats
 * and memory held back before any grant), plus the model bindings and Wardens the Forage map and
 * flow need, and the Forage requests seen on the stream with the Queen's answer. Everything is the
 * generated schema as received; the only shapes declared here are the ones the view itself adds.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/slice/`. Imported by `reduce.ts`, `requests.ts`
 *   and, through the package face, by `use_forage.ts` and every table and diagram of the view.
 *
 * Key invariants:
 *   - A schema shape is never redeclared by hand; these are aliases of the generated types.
 *   - `requests` is newest first and bounded to `MAX_REQUESTS`.
 *
 * See Also:
 *   - docs/observation/design.md section 5.3.
 */
import type { components } from '../../../landing_board/types';

type Schemas = components['schemas'];
export type ForageSnapshot = Schemas['ForageView'];
export type CapacityView = Schemas['CapacityView'];
export type GrantView = Schemas['GrantView'];
export type HeadroomView = Schemas['HeadroomView'];
export type ReserveView = Schemas['ReserveView'];
export type LlmView = Schemas['LlmView'];
export type ProviderView = Schemas['ProviderView'];
export type SlotView = Schemas['SlotView'];
export type WardenView = Schemas['WardenView'];
export type WardenList = Schemas['WardenList'];
export type ForageFrame = Schemas['ForageFrame'];
export type TrailEventView = Schemas['TrailEventView'];

export const MAX_REQUESTS = 100; // A phone's memory is the limit; older answers fall off the end.
export const MAX_GONE = 200; // Revoked grant ids remembered so a late seed cannot revive them.

// The Queen's answer to a Forage request: none yet, grant (in full or part) or denial.
export type Decision = 'pending' | 'granted' | 'denied';

// One `ForageRequest` as seen on the trail: asked, and answered once the Queen's event lands.
export interface RequestRecord {
  // The `forage.requested` event's id, unique per ask.
  id: string;
  grantId: string;
  requestKind: string | null;
  requestedAt: string;
  decision: Decision;
  decidedAt: string | null;
  // The Queen's reason code on a denial; null when granted or still pending.
  reason: string | null;
}

export interface ForageState {
  seeded: boolean;
  capacities: readonly CapacityView[];
  grants: readonly GrantView[];
  headroom: HeadroomView | null;
  reserve: ReserveView | null;
  llm: LlmView | null;
  wardens: readonly WardenView[];
  requests: readonly RequestRecord[];
  // Grants a frame revoked or expired, so a seed that was read earlier cannot bring them back.
  gone: readonly string[];
  // Counts `forage.capacity_reported` frames; the hook re-reads the ledger when it grows.
  capacityTicks: number;
  // True once a frame landed before the seed did (the seed then yields to the frames).
  framed: boolean;
  // The last failed read, shown through `ErrorNotice`; null when well.
  error: unknown;
}

export type ForageEvent =
  | { type: 'open' }
  | { type: 'seeded'; forage: ForageSnapshot; wardens: WardenList; llm: LlmView }
  | { type: 'reseeded'; forage: ForageSnapshot }
  | { type: 'failed'; error: unknown }
  | { type: 'frame'; frame: ForageFrame };

export const initialForage: ForageState = {
  seeded: false,
  capacities: [],
  grants: [],
  headroom: null,
  reserve: null,
  llm: null,
  wardens: [],
  requests: [],
  gone: [],
  capacityTicks: 0,
  framed: false,
  error: null,
};
