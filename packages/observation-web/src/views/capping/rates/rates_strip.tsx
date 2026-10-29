/**
 * `RatesStrip`: one card per risk tier with its proposals, the gate's outcomes and its audit rates.
 *
 * Tier names are the gate's `RiskTier` as what the action does (`writes inside its scratch
 * folder`, the code in the heading's `title`), shown as text and never as the
 * Cell tier badge, which is a different scale. Each card is a definition list so a screen reader
 * hears every figure with its label. Findings are audits the judge rejected, and the percentages
 * are the server's fractions (audited of finished, rejected of scored).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/rates/`. Drawn by `capping_screen.tsx` from the
 *   slice's counts and rates.
 *
 * Key invariants:
 *   - Read-only and stateless: it draws its props.
 *   - A tier with nothing finished says "Audits: none yet" in place of the three audit figures.
 *
 * See Also:
 *   - src/views/capping/rates/model.ts for the arithmetic.
 */
import type { ReactElement } from 'react';
import { riskTierWords } from '../../../components';
import { wordsOf } from '../slice';
import type { CappingState } from '../slice';
import { percent, tierRates } from './model';
import type { TierRates } from './model';

function Figure(props: { label: string; value: string | number }): ReactElement {
  return (
    <div className="capping-figure">
      <dt>{props.label}</dt>
      <dd>{props.value}</dd>
    </div>
  );
}

function TierCard(props: { rates: TierRates }): ReactElement {
  const r = props.rates;
  return (
    <li className="capping-rate comb-cell">
      <h3 title={wordsOf(r.tier)}>{riskTierWords(r.tier)}</h3>
      <dl className="capping-figures">
        <Figure label="Proposals" value={r.proposals} />
        <Figure label="Waiting" value={r.waiting} />
        <Figure label="Approved" value={r.approved} />
        <Figure label="Refused" value={r.refused} />
        <Figure label="Rolled back" value={r.rolledBack} />
        {r.finished === 0 ? (
          // Nothing finished means nothing to audit: one plain line instead of `0 of 0 (-)`.
          <Figure label="Audits" value="none yet" />
        ) : (
          <>
            <Figure
              label="Audited"
              value={`${r.audited} of ${r.finished} (${percent(r.sampled)})`}
            />
            <Figure label="Findings" value={`${r.findings} (${percent(r.rejection)} of scored)`} />
            <Figure label="Unscored" value={r.inconclusive} />
          </>
        )}
      </dl>
    </li>
  );
}

export function RatesStrip(props: { state: CappingState }): ReactElement {
  const tiers = tierRates(props.state);
  return (
    <section className="capping-section" aria-label="Rates per tier">
      <h2 className="section-heading">Rates per tier</h2>
      <ul className="capping-rates">
        {tiers.map((rates) => (
          <TierCard key={rates.tier} rates={rates} />
        ))}
      </ul>
    </section>
  );
}
