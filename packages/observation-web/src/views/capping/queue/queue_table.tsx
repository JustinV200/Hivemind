/**
 * `QueueTable`: the Hive-wide Capping queue, one row per proposal, sortable by header.
 *
 * Each row shows the proposal, its risk tier, its state, the bee that proposed it (a link to that
 * bee's thoughts), its task, its Cell (a link to the Cell page when the schema names one), the
 * gate's verdict and the reason for it, the rollback mark and, for an Exoskeleton action, the
 * recording column's note. The default order is the newest change first. Under 1080 px the table
 * becomes stacked cards (one proposal, one card) by stylesheet alone, each cell keeping its label.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/queue/`. Drawn by `capping_screen.tsx` from the
 *   filtered rows; the only state is the sort the reader chose.
 *
 * Key invariants:
 *   - Read-only: sorting reorders what is drawn and nothing else.
 *   - Rows are keyed by the proposal's id, so a hop updates a row in place.
 *   - State, verdict and the rollback mark are words as well as colour.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 5.9.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import { wordsOf } from '../slice';
import type { ProposalState } from '../slice';
import { BeeLink, CellLink, RecordingNote } from './links';
import { NONE, reasonOf, rollbackOf, sortRows } from './rows';
import type { QueueColumn, QueueRow } from './rows';
import { SortHeader } from './sort_header';

interface Sort {
  column: QueueColumn;
  descending: boolean;
}

// Header labels left to right; a column with a sort key sorts, the others (the id, the reason, the
// rollback text, the recording note) have no useful order and stay plain text.
const HEADERS: readonly { label: string; column: QueueColumn | null }[] = [
  { label: 'Proposal', column: null },
  { label: 'Tier', column: 'tier' },
  { label: 'State', column: 'state' },
  { label: 'Bee', column: 'bee' },
  { label: 'Task', column: 'task' },
  { label: 'Cell', column: 'cell' },
  { label: 'Verdict', column: 'verdict' },
  { label: 'Reason', column: null },
  { label: 'Rollback', column: null },
  { label: 'Recording', column: null },
  { label: 'Updated', column: 'updated' },
];

type Tone = 'ok' | 'warn' | 'alarm' | 'idle';

const STATE_TONE: Readonly<Record<ProposalState, Tone>> = {
  PROPOSED: 'idle',
  CHECKING: 'idle',
  CAPPED: 'ok',
  APPLIED: 'ok',
  VERIFIED: 'ok',
  REJECTED: 'alarm',
  ROLLED_BACK: 'warn',
};

function Tag(props: { tone: Tone; children: string }): ReactElement {
  return <span className={`capping-tag capping-tag--${props.tone}`}>{props.children}</span>;
}

function Row(props: { row: QueueRow }): ReactElement {
  const { proposal, verdict, exoskeleton } = props.row;
  return (
    <tr>
      <td data-label="Proposal">
        <IdToken id={proposal.id} />
      </td>
      <td data-label="Tier">{wordsOf(proposal.tier)}</td>
      <td data-label="State">
        <Tag tone={STATE_TONE[proposal.state]}>{wordsOf(proposal.state)}</Tag>
      </td>
      <td data-label="Bee">
        <BeeLink bee={proposal.bee_id} />
      </td>
      <td data-label="Task">{proposal.task_id ? <IdToken id={proposal.task_id} /> : NONE}</td>
      <td data-label="Cell">{proposal.cell_id ? <CellLink cell={proposal.cell_id} /> : NONE}</td>
      <td data-label="Verdict">
        {verdict ? (
          <Tag tone={verdict.verdict === 'CAPPED' ? 'ok' : 'alarm'}>{verdict.verdict}</Tag>
        ) : (
          NONE
        )}
      </td>
      <td data-label="Reason">{reasonOf(props.row)}</td>
      <td data-label="Rollback">{rollbackOf(proposal)}</td>
      <td data-label="Recording">{exoskeleton ? <RecordingNote /> : NONE}</td>
      <td data-label="Updated">
        <TimeStamp at={proposal.updated_at} />
      </td>
    </tr>
  );
}

export function QueueTable(props: { rows: readonly QueueRow[] }): ReactElement {
  const [sort, setSort] = useState<Sort>({ column: 'updated', descending: true });
  const rows = sortRows(props.rows, sort.column, sort.descending);
  // Choosing the sorted column again reverses it; choosing another starts ascending, except that
  // time starts newest first, because that is what a queue is read for.
  const choose = (column: QueueColumn): void =>
    setSort((now) => ({
      column,
      descending: now.column === column ? !now.descending : column === 'updated',
    }));
  return (
    <table className="capping-table">
      <caption>Capping queue: every proposal and where the gate has it</caption>
      <thead>
        <tr>
          {HEADERS.map(({ label, column }) =>
            column === null ? (
              <th key={label} scope="col" className="capping-plain">
                {label}
              </th>
            ) : (
              <SortHeader
                key={label}
                label={label}
                sorted={
                  sort.column === column ? (sort.descending ? 'descending' : 'ascending') : null
                }
                onSort={() => choose(column)}
              />
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <Row key={row.proposal.id} row={row} />
        ))}
      </tbody>
    </table>
  );
}
