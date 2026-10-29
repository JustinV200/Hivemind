/**
 * `WaxPanel`: the Cell's Cell Wax, highest severity first, with who proposed it and when it lapses.
 *
 * Cell Wax is the notes a Cell carries for the bees that work in it: a `NOTE`, a `CAUTION` or a
 * `BLOCK`. Each row shows the severity in words, its state (`proposed` or `written`), who noticed it
 * (a bee, a Patrol or the human), when it was proposed and when it lapses. Standing wax, which
 * never lapses, says so. The page's row carries no title or text, so each row links to the note
 * itself in the Honey browser (`#/honey/cells/<id>/wax?item=<wax id>`), where both are.
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
import { hrefFor } from '../../../router';
import type { CellPage } from '../slice';

// The note's own page: the Honey browser's wax folder for this Cell, with the note open in the
// reading pane, which is where its title and text are (the page row carries neither).
const waxHref = (cellId: string, waxId: string): string =>
  hrefFor({ name: 'honey', path: `cells/${cellId}/wax`, item: waxId });
import { Panel, Tag } from './panel';
import type { TagTone } from './panel';

type Entry = CellPage['wax'][number];

const SEVERITY_TONE: Readonly<Record<Entry['severity'], TagTone>> = {
  NOTE: 'idle',
  CAUTION: 'warn',
  BLOCK: 'alarm',
};

export function WaxPanel(props: { page: CellPage }): ReactElement {
  const { wax, cell } = props.page;
  return (
    <Panel id="wax" title="Cell Wax">
      {wax.length === 0 ? (
        <p>No Cell Wax. A note appears here when a bee or a Patrol leaves one.</p>
      ) : (
        <p className="cell-muted">Notes left on this Cell for the bees that work in it.</p>
      )}
      <ul className="cell-list">
        {wax.map((entry) => (
          <li key={entry.id} className="cell-row">
            <Tag tone={SEVERITY_TONE[entry.severity]}>{entry.severity}</Tag>
            <IdToken id={entry.id} />
            <span>{entry.state.toLowerCase()}</span>
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
            <a href={waxHref(cell.id, entry.id)}>read the note</a>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
