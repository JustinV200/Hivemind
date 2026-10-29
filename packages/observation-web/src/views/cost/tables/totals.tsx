/**
 * `Totals`: the strip across the top of the Cost view: total spend, hosted spend, the local saving.
 *
 * Total spend is every completed call counted. Hosted spend is the part that cost money; the rest
 * was served free by a local model. The local saving is the slots' savings together: the hosted
 * spend that would be zero if the slots ran local, an upper bound the strip says in words. A line
 * under the strip says the window: everything on record, as of the answer's own time.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Drawn by `cost_screen.tsx` from the
 *   slice's snapshot.
 *
 * Key invariants:
 *   - Each figure is a term and its value in a description list, so it reads without the layout.
 *
 * See Also:
 *   - docs/observation/design.md section 5.8.
 */
import type { ReactElement } from 'react';
import { TimeStamp } from '../../../components';
import { count, usd } from '../format';
import type { CostSnapshot } from '../slice';
import { hostedCalls, hostedTotal } from './rows';

function Figure(props: { term: string; value: string; note?: string }): ReactElement {
  return (
    <div className="cost-figure comb-cell">
      <dt>{props.term}</dt>
      <dd>{props.value}</dd>
      {props.note ? <dd className="cost-note">{props.note}</dd> : null}
    </div>
  );
}

export function Totals(props: { cost: CostSnapshot }): ReactElement {
  const { cost } = props;
  const calls = hostedCalls(cost);
  return (
    <>
      <dl className="cost-totals" aria-label="Totals">
        <Figure term="Total spend" value={usd(cost.total_usd)} />
        <Figure
          term="Hosted spend"
          value={usd(hostedTotal(cost))}
          note={`${count(calls.hosted)} of ${count(calls.all)} calls cost money`}
        />
        <Figure
          term="If local, saves"
          value={usd(cost.if_local_saving_usd)}
          note="At most: the hosted spend that would be zero on a local model"
        />
      </dl>
      {/* The window: everything on record up to the answer's moment, which the schema dates. */}
      {cost.as_of ? (
        <p className="cost-note">
          Everything on record, as of <TimeStamp at={cost.as_of} />.
        </p>
      ) : null}
    </>
  );
}
