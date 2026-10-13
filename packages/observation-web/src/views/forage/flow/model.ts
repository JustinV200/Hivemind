/**
 * The flow model: the Queen's shared pool, each Warden's grants, and what each grant has spent.
 *
 * The flow has three columns. The pool is the Royal Reserve, what the Queen has granted and what is
 * still free; the Wardens each hold the grants they were issued; each grant is the unit of spend,
 * since a grant is issued for one task. One unit at a time sets what the bars measure: model seats,
 * sub-bee slots, spend in US dollars or tokens. Only the figures the schema carries are used: the
 * pool's reserve and free figures exist for seats and sub-bees but not for spend or tokens, and a
 * grant's use exists for spend and tokens but not for seats, so those parts are left out.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/flow/`. Built by `forage_screen.tsx` from the
 *   slice; laid out by `layout.ts` and drawn by `diagram.tsx`.
 *
 * Key invariants:
 *   - Pure: Wardens sorted by Cell id then id, grants by id; the same figures give the same model.
 *   - `used` is null where the schema has no use figure for the unit, never zero.
 *   - `free` is null where the schema has no pool figure for the unit.
 *
 * See Also:
 *   - docs/observation/design.md section 4.7.
 */
import { isNearExpiry } from '../tables/expiry';
import { count, usd } from '../format';
import type { GrantView, HeadroomView, ReserveView, WardenView } from '../slice';

export type FlowUnit = 'seats' | 'sub_bees' | 'spend' | 'tokens';

export const FLOW_UNITS: readonly { unit: FlowUnit; label: string }[] = [
  { unit: 'seats', label: 'Model seats' },
  { unit: 'sub_bees', label: 'Sub-bees' },
  { unit: 'spend', label: 'Spend (USD)' },
  { unit: 'tokens', label: 'Tokens' },
];

export interface FlowGrant {
  id: string;
  label: string;
  amount: number;
  used: number | null;
  expiresAt: string;
  near: boolean;
}

export interface FlowWarden {
  id: string;
  cellId: string;
  state: string | null;
  amount: number;
  used: number | null;
  grants: FlowGrant[];
}

export interface FlowModel {
  unit: FlowUnit;
  reserve: number;
  granted: number;
  free: number | null;
  wardens: FlowWarden[];
}

export interface FlowInput {
  unit: FlowUnit;
  grants: readonly GrantView[];
  headroom: HeadroomView | null;
  reserve: ReserveView | null;
  wardens: readonly WardenView[];
  nowMs: number;
}

const round2 = (n: number): number => Math.round(n * 100) / 100;

// How one unit reads off a grant: the amount issued, and what has been used where that is known.
const MEASURE: Readonly<
  Record<FlowUnit, { amount: (g: GrantView) => number; used: ((g: GrantView) => number) | null }>
> = {
  seats: { amount: (g) => g.seats, used: null },
  sub_bees: { amount: (g) => g.max_sub_bees, used: null },
  spend: { amount: (g) => g.spend_budget, used: (g) => g.spent },
  tokens: { amount: (g) => g.token_budget, used: (g) => g.tokens_spent },
};

export const formatAmount = (unit: FlowUnit, n: number): string =>
  unit === 'spend' ? usd(n) : count(n);

function flowGrant(grant: GrantView, input: FlowInput): FlowGrant {
  const measure = MEASURE[input.unit];
  return {
    id: grant.id,
    label: grant.task_id ?? grant.id,
    amount: round2(measure.amount(grant)),
    used: measure.used ? round2(measure.used(grant)) : null,
    expiresAt: grant.expires_at,
    near: isNearExpiry(grant.expires_at, input.nowMs),
  };
}

function flowWarden(holder: string, grants: readonly GrantView[], input: FlowInput): FlowWarden {
  const mine = grants.map((g) => flowGrant(g, input)).sort((a, b) => (a.id < b.id ? -1 : 1));
  const known = input.wardens.find((w) => w.id === holder);
  const measured = input.unit === 'spend' || input.unit === 'tokens';
  return {
    id: holder,
    cellId: grants[0]?.cell_id ?? known?.cell_id ?? '',
    state: known?.state ?? null,
    amount: round2(mine.reduce((a, g) => a + g.amount, 0)),
    used: measured ? round2(mine.reduce((a, g) => a + (g.used ?? 0), 0)) : null,
    grants: mine,
  };
}

// The pool figures the schema has for the unit: seats and sub-bees only.
function poolOf(input: FlowInput): Pick<FlowModel, 'reserve' | 'free'> {
  if (input.unit === 'seats') {
    return { reserve: input.reserve?.seats ?? 0, free: input.headroom?.shared_seats ?? null };
  }
  if (input.unit === 'sub_bees') return { reserve: 0, free: input.headroom?.sub_bees ?? null };
  // Spend and tokens have no pool figure: the Queen's headroom counts seats and sub-bee slots.
  return { reserve: 0, free: null };
}

export function flowModel(input: FlowInput): FlowModel {
  const byHolder = new Map<string, GrantView[]>();
  // Group the live grants under the Warden that holds them.
  for (const grant of input.grants) {
    byHolder.set(grant.holder, [...(byHolder.get(grant.holder) ?? []), grant]);
  }
  const wardens = [...byHolder.entries()]
    .map(([holder, grants]) => flowWarden(holder, grants, input))
    .sort((a, b) => (a.cellId < b.cellId ? -1 : a.cellId > b.cellId ? 1 : a.id < b.id ? -1 : 1));
  const granted = round2(wardens.reduce((a, w) => a + w.amount, 0));
  return { unit: input.unit, granted, wardens, ...poolOf(input) };
}
