/**
 * `Legend`: the fixed key to the three Comb Shield levels, drawn above the Fleet table.
 *
 * The tier badge is on every row, and this says what each level means: `MEADOW`, `PROPOLIS` and
 * `NIGHT VEIL`, each as its own pill with the schema's one-line meaning beside it. It is not
 * collapsible and not a tooltip, and it is drawn before the first answer too (the pills are
 * known; only their meanings wait for the aggregate), so the key is on screen from the first paint.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Used by `fleet.tsx`; styled by `fleet.css`.
 *
 * Key invariants:
 *   - Always three entries, in the order open to closed.
 *   - The meanings are the aggregate's `legend` text and nothing written here.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.1 and 5.1; codingrules 8.11.
 */
import type { ReactElement } from 'react';
import { TierBadge } from '../../components';
import type { CombShieldLevel } from '../../components';
import type { TierLegendEntry } from './slice';

// The README's order, from open to closed; the pills do not depend on the answer.
const TIERS: readonly CombShieldLevel[] = ['MEADOW', 'PROPOLIS', 'NIGHT_VEIL'];

export function Legend(props: { entries: readonly TierLegendEntry[] }): ReactElement {
  return (
    <section className="fleet-legend" aria-labelledby="fleet-legend-title">
      <h2 id="fleet-legend-title">Comb Shield levels</h2>
      <ul>
        {TIERS.map((tier) => (
          <li key={tier}>
            <TierBadge level={tier} />
            <span>{props.entries.find((entry) => entry.tier === tier)?.meaning ?? ''}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
