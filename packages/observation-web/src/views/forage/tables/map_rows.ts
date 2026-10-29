/**
 * The Forage map rows: every model source the Hive can reach, with what the schema says about it.
 *
 * `GET /v1/llm` lists the configured providers and the slot bindings that name a model on one. A
 * source here is one binding (the slot's key, its model, its provider and the provider's kind and
 * health, what it serves, its effort and its fallback), and a provider no slot uses still gets a
 * row of its own, so every source is on the map. The schema does not carry a source's grade,
 * measured grade, abundance, cost or per-Cell distance yet, so no column pretends to.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Built by `map_table.tsx` from the slice's
 *   `llm`; sorted here so the order is a pure, testable function.
 *
 * Key invariants:
 *   - Pure and stable: equal inputs give equal rows; a tie in the sorted column breaks on the key.
 *   - A missing value is the hyphen `-`, never an empty cell.
 *
 * See Also:
 *   - docs/observation/design.md section 5.3 (the Forage map).
 */
import type { LlmView } from '../slice';

export const NONE = '-';

export interface MapRow {
  // Slot key, or `provider:<name>` for a provider no slot binds.
  key: string;
  slot: string;
  model: string;
  provider: string;
  kind: string;
  health: string;
  serves: string;
  effort: string;
  fallback: string;
}

export type MapColumn = Exclude<keyof MapRow, 'key'>;

// The headers in plain words: a slot is the model a kind of bee runs on, bound to a provider.
export const MAP_COLUMNS: readonly { column: MapColumn; label: string }[] = [
  { column: 'slot', label: 'Slot' },
  { column: 'model', label: 'Model' },
  { column: 'provider', label: 'Provider' },
  { column: 'kind', label: 'Provider kind' },
  { column: 'health', label: 'Health' },
  { column: 'serves', label: 'Which bees use it' },
  { column: 'effort', label: 'Effort (speed vs care)' },
  { column: 'fallback', label: 'If it fails, use' },
];

export function mapRows(llm: LlmView): MapRow[] {
  const providers = new Map(llm.providers.map((p) => [p.name, p]));
  const bound = llm.slots.map((slot): MapRow => {
    const provider = providers.get(slot.provider);
    return {
      key: slot.key,
      slot: slot.key,
      model: slot.model,
      provider: slot.provider,
      kind: provider?.kind ?? NONE,
      health: provider?.health ?? NONE,
      serves: slot.serves ?? NONE,
      effort: slot.effort,
      fallback: slot.fallback ?? NONE,
    };
  });
  const used = new Set(llm.slots.map((s) => s.provider));
  // A provider no binding names is still a source the Hive can reach, so it gets its own row.
  const idle = llm.providers
    .filter((p) => !used.has(p.name))
    .map((p): MapRow => ({
      key: `provider:${p.name}`,
      slot: NONE,
      model: NONE,
      provider: p.name,
      kind: p.kind,
      health: p.health,
      serves: NONE,
      effort: NONE,
      fallback: NONE,
    }));
  return [...bound, ...idle];
}

// Sorts by one column in the given direction; ties break on the row key, so the order is total.
export function sortMapRows(
  rows: readonly MapRow[],
  column: MapColumn,
  descending: boolean,
): MapRow[] {
  const sign = descending ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = a[column];
    const y = b[column];
    const order = x < y ? -1 : x > y ? 1 : 0;
    return order * sign || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
  });
}
