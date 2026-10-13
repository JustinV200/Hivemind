/**
 * `SpendGauges`: what each Warden's grants have spent against what they allow, one gauge each.
 *
 * Spend is the one Forage dimension every Cell draws from a single wallet. The schema carries each
 * grant's spend budget (its cap) and what has been charged to it, so this adds them up per Warden
 * and draws the shared `Gauge`: green to 70 percent, amber to 90, red above. A Warden with no
 * budget issued shows an empty gauge, not a hidden one. The Hive-wide daily caps live on the cost
 * and device views, not in the Forage reads, so they are not drawn here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Drawn by `forage_screen.tsx` from the
 *   slice's grants.
 *
 * Key invariants:
 *   - Read-only and stateless; one gauge per Warden, sorted by Warden id.
 *   - Sums are rounded to cents so floating-point noise never shows.
 *
 * See Also:
 *   - src/components/gauge.tsx for the gauge itself.
 */
import type { ReactElement } from 'react';
import { Gauge, IdToken } from '../../../components';
import type { GrantView } from '../slice';

const cents = (n: number): number => Math.round(n * 100) / 100;

export interface WardenSpend {
  holder: string;
  spent: number;
  budget: number;
}

// Totals each Warden's spent and allowed dollars across its live grants.
export function spendByWarden(grants: readonly GrantView[]): WardenSpend[] {
  const totals = new Map<string, WardenSpend>();
  for (const grant of grants) {
    const now = totals.get(grant.holder) ?? { holder: grant.holder, spent: 0, budget: 0 };
    totals.set(grant.holder, {
      holder: grant.holder,
      spent: cents(now.spent + grant.spent),
      budget: cents(now.budget + grant.spend_budget),
    });
  }
  return [...totals.values()].sort((a, b) => (a.holder < b.holder ? -1 : 1));
}

export function SpendGauges(props: { grants: readonly GrantView[] }): ReactElement {
  return (
    <ul className="forage-spend">
      {spendByWarden(props.grants).map((w) => (
        <li key={w.holder}>
          <IdToken id={w.holder} />
          <Gauge used={w.spent} budget={w.budget} label={`Spend of ${w.holder}, US dollars`} />
        </li>
      ))}
    </ul>
  );
}
