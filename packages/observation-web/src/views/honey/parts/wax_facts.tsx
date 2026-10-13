/**
 * `WaxFacts`: a Cell Wax note's severity, proposer and expiry, as one line of labelled facts.
 *
 * A wax note weighs on placement (`NOTE`, `CAUTION`, `BLOCK`), was proposed by a bee or by the
 * human, and either lapses on its own or stands until the Queen clears it. Other kinds of item
 * carry none of these, so the line is drawn for wax only. The severity is a word in a tag, never a
 * colour alone.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/parts/`. Used by item rows and the reading pane.
 *
 * Key invariants:
 *   - Draws nothing for an item whose kind is not `wax`.
 *   - A null proposer reads "the human" and a null expiry reads "no expiry", as the schema says.
 *
 * See Also:
 *   - README.md (Cell Wax) for the severities.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import type { ItemSummary } from '../slice';
import { Fact } from './fact';

type WaxFields = Pick<ItemSummary, 'kind' | 'severity' | 'proposer' | 'expires_at'>;

export function WaxFacts(props: { item: WaxFields }): ReactElement | null {
  const { kind, severity, proposer, expires_at } = props.item;
  // Only wax notes have a weight, a proposer and an expiry; nothing is invented for the rest.
  if (kind !== 'wax') return null;
  return (
    <p className="honey-wax">
      {severity ? (
        <Fact label="Severity">
          <span className={`honey-severity honey-severity--${severity.toLowerCase()}`}>
            {severity}
          </span>
        </Fact>
      ) : null}
      <Fact label="Proposed by">{proposer ? <IdToken id={proposer} /> : 'the human'}</Fact>
      <Fact label="Expires">{expires_at ? <TimeStamp at={expires_at} /> : 'no expiry'}</Fact>
    </p>
  );
}
