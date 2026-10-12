/**
 * `KindFilter`: the three-way Real / Virtual / All control in the Fleet's title row.
 *
 * A segmented control of three toggle buttons, one pressed at a time. It is a filter and nothing
 * more: it changes which rows are drawn, never what is read.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/filter/`. Used by `fleet.tsx`; styled by
 *   `fleet.css`.
 *
 * Key invariants:
 *   - Exactly one button has `aria-pressed="true"`.
 *   - Every button has a visible word as its label.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 */
import type { ReactElement } from 'react';
import type { KindChoice as Kind } from './use_kind_filter';

const CHOICES: readonly { kind: Kind; label: string }[] = [
  { kind: 'REAL', label: 'Real' },
  { kind: 'VIRTUAL', label: 'Virtual' },
  { kind: 'ALL', label: 'All' },
];

export function KindFilter(props: { kind: Kind; onChoose: (kind: Kind) => void }): ReactElement {
  return (
    <div className="fleet-filter" role="group" aria-label="Show Cells of kind">
      {CHOICES.map(({ kind, label }) => (
        <button
          key={kind}
          type="button"
          className="fleet-filter-button"
          aria-pressed={props.kind === kind}
          onClick={() => props.onChoose(kind)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
