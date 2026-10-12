/**
 * `WaxPanel`: the Cell's Cell Wax, highest severity first, with who proposed it and when it lapses.
 *
 * Cell Wax is the notes a Cell carries for the bees that work in it: a `NOTE`, a `CAUTION` or a
 * `BLOCK`. Each row shows the severity in words, its state (`PROPOSED` or `WRITTEN`), who noticed it
 * (a bee, a Patrol or the human), when it was proposed and when it lapses. Standing wax, which
 * never lapses, says so.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx` from the page.
 *
 * Key invariants:
 *   - The order is the page's (highest severity first); the panel does not re-sort.
 *   - A human's note has no proposer id; it shows the origin `HUMAN` in its place.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import type { CellPage } from '../slice';
import { Panel, Tag } from './panel';
import type { TagTone } from './panel';

type Entry = CellPage['wax'][number];

const SEVERITY_TONE: Readonly<Record<Entry['severity'], TagTone>> = {
  NOTE: 'idle',
  CAUTION: 'warn',
  BLOCK: 'alarm',
};

export function WaxPanel(props: { page: CellPage }): ReactElement {
  const { wax } = props.page;
  return (
    <Panel id="wax" title="Cell Wax">
      {wax.length === 0 ? (
        <p>No Cell Wax. A note appears here when a bee or a Patrol leaves one.</p>
      ) : null}
      <ul className="cell-list">
        {wax.map((entry) => (
          <li key={entry.id} className="cell-row">
            <Tag tone={SEVERITY_TONE[entry.severity]}>{entry.severity}</Tag>
            <IdToken id={entry.id} />
            <span>{entry.state}</span>
            <span>{`by ${entry.proposer ?? entry.origin}`}</span>
            <span>
              proposed <TimeStamp at={entry.proposed_at} />
            </span>
            <span>
              {entry.expires_at === null ? (
                'standing'
              ) : (
                <>
                  lapses <TimeStamp at={entry.expires_at} />
                </>
              )}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
