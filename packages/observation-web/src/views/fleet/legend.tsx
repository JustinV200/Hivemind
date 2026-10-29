/**
 * `Legend`: the fixed key to the three Comb Shield levels, drawn above the Fleet table.
 *
 * The tier badge is on every row, and this says what each level means: `MEADOW`, `PROPOLIS` and
 * `NIGHT VEIL`, each as its own pill with the schema's one-line meaning beside it. It is not
 * collapsible and not a tooltip, and it is drawn before the first answer too, with the Entrance's
 * fixed sentences as the fallback, so the key is on screen from the first paint and stays whole
 * when the read is refused.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Used by `fleet.tsx`; styled by `fleet.css`.
 *
 * Key invariants:
 *   - Always three entries, in the order open to closed.
 *   - The meanings are the aggregate's `legend` text when it has answered; before that, or when
 *     it was refused, the Entrance's own three sentences.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.1 and 5.1; codingrules 8.11.
 */
import type { ReactElement } from 'react';
import { TierBadge } from '../../components';
import type { CombShieldLevel } from '../../components';
import { hrefFor } from '../../router';
import type { TierLegendEntry } from './slice';

// The glossary entry on the levels, for the reader who wants more than a line each.
const HELP_HREF = hrefFor({ name: 'help', page: 'glossary', section: 'comb-shield-level' });

// The README's order, from open to closed; the pills do not depend on the answer.
const TIERS: readonly CombShieldLevel[] = ['MEADOW', 'PROPOLIS', 'NIGHT_VEIL'];

// The Entrance's own three sentences (hivemind.observation.api.fleet), kept here so the legend
// still explains the badges when the fleet read is refused and no answer carries them.
const FALLBACK: Readonly<Record<CombShieldLevel, string>> = {
  MEADOW: 'Baseline: any machine, no VPN required.',
  PROPOLIS: 'Hardened: OpenVPN required, Tor disabled.',
  NIGHT_VEIL: 'Virtual only: OpenVPN plus Tor, every slot local, torn down never kept.',
};

export function Legend(props: { entries: readonly TierLegendEntry[] }): ReactElement {
  return (
    <section className="fleet-legend" aria-labelledby="fleet-legend-title">
      <h2 id="fleet-legend-title">Comb Shield levels</h2>
      <a href={HELP_HREF}>More in Help</a>
      <ul>
        {TIERS.map((tier) => (
          <li key={tier}>
            <TierBadge level={tier} />
            <span>
              {props.entries.find((entry) => entry.tier === tier)?.meaning ?? FALLBACK[tier]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
