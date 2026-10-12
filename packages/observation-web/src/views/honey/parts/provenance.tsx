/**
 * `Provenance`: where an item came from, as one line of labelled facts.
 *
 * Provenance is the Honey browser's organising idea: every item says which task, Cell and bee it
 * came from, how it came (the Nectar origin or the wax note's origin) and when. A fact the schema
 * leaves null is left out rather than shown as a blank, and the labels are words so a screen
 * reader hears what each id is.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/parts/`. Used by item rows, search hits and the
 *   reading pane (for the item and for each of a Honey row's other sources).
 *
 * Key invariants:
 *   - Draws only what `HoneyProvenanceView` carries.
 *   - Ids are shown through `IdToken`, so the full id is always in the title.
 *
 * See Also:
 *   - src/components/id_token.tsx and time_stamp.tsx.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import type { ProvenanceView } from '../slice';
import { Fact } from './fact';

export function Provenance(props: { source: ProvenanceView }): ReactElement {
  const { task_id, cell_id, bee, source, observed_at } = props.source;
  return (
    <p className="honey-provenance">
      {task_id ? (
        <Fact label="Task">
          <IdToken id={task_id} />
        </Fact>
      ) : null}
      {cell_id ? (
        <Fact label="Cell">
          <IdToken id={cell_id} />
        </Fact>
      ) : null}
      {bee ? (
        <Fact label="Bee">
          <IdToken id={bee} />
        </Fact>
      ) : null}
      {source ? (
        <Fact label="Source">
          <code>{source}</code>
        </Fact>
      ) : null}
      <Fact label="When">
        <TimeStamp at={observed_at} />
      </Fact>
    </p>
  );
}
