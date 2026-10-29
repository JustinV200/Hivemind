/**
 * `QueueTable`: the Hive-wide Capping queue, one row per proposal, sortable by header.
 *
 * Each row shows the proposal, its risk tier, its state, the bee that proposed it (a link to that
 * bee's thoughts), its task, its Cell (a link to the Cell page when the schema names one), the
 * gate's verdict and the reason for it, the rollback mark and, for an Exoskeleton action, the
 * recording column's note. The default order is the newest change first. Under 1280 px the table
 * becomes stacked cards (one proposal, one card), each cell keeping its label, the cells with
 * nothing to say left off the card, and the shared "Sort by" control stands in for the hidden
 * header row. A proposal the gate has not judged yet says "not decided yet" under Verdict.
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
import type { ReactElement, ReactNode } from 'react';
import {
  IdToken,
  proposalStateWords,
  riskTierWords,
  SortSelect,
  TimeStamp,
  useMinWidth,
  WIDE_TABLE_MIN,
} from '../../../components';
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

// A state or verdict in the words the rates cards use, with the gate's code on the title.
function Tag(props: { tone: Tone; code: string }): ReactElement {
  return (
    <span className={`tag tag--status tag--${props.tone}`} title={props.code}>
      {proposalStateWords(props.code)}
    </span>
  );
}

// A cell with nothing to say keeps the hyphen in the table and is left off the card, so three
// proposals on a phone are not thirty rows of `-`.
function Cell(props: { label: string; children: ReactNode }): ReactElement {
  const empty = props.children === NONE;
  return (
    <td data-label={props.label} className={empty ? 'capping-empty' : undefined}>
      {props.children}
    </td>
  );
}

// A proposal the gate has not judged says so, rather than showing a hyphen under "Verdict".
const WAITING: ReadonlySet<ProposalState> = new Set(['PROPOSED', 'CHECKING']);

function Row(props: { row: QueueRow }): ReactElement {
  const { proposal, verdict, exoskeleton } = props.row;
  // The rollback's words carry the method's code on their title, where there is a method.
  const rollback = rollbackOf(proposal);
  return (
    <tr>
      <td data-label="Proposal">
        <IdToken id={proposal.id} />
      </td>
      <td data-label="Tier" title={proposal.tier}>
        {riskTierWords(proposal.tier)}
      </td>
      <td data-label="State">
        <Tag tone={STATE_TONE[proposal.state]} code={proposal.state} />
      </td>
      <td data-label="Bee">
        <BeeLink bee={proposal.bee_id} />
      </td>
      <Cell label="Task">{proposal.task_id ? <IdToken id={proposal.task_id} /> : NONE}</Cell>
      <Cell label="Cell">{proposal.cell_id ? <CellLink cell={proposal.cell_id} /> : NONE}</Cell>
      <Cell label="Verdict">
        {verdict ? (
          <Tag tone={verdict.verdict === 'CAPPED' ? 'ok' : 'alarm'} code={verdict.verdict} />
        ) : WAITING.has(proposal.state) ? (
          'not decided yet'
        ) : (
          NONE
        )}
      </Cell>
      <Cell label="Reason">{reasonOf(props.row)}</Cell>
      <Cell label="Rollback">
        {rollback !== NONE && proposal.rollback_method ? (
          <span title={proposal.rollback_method}>{rollback}</span>
        ) : (
          rollback
        )}
      </Cell>
      <Cell label="Recording">{exoskeleton ? <RecordingNote /> : NONE}</Cell>
      <td data-label="Updated">
        <TimeStamp at={proposal.updated_at} />
      </td>
    </tr>
  );
}

// The shared "Sort by" control over the sortable headers, drawn while the rows are cards.
function QueueSort(props: { sort: Sort; onChange: (sort: Sort) => void }): ReactElement {
  const { sort } = props;
  return (
    <SortSelect
      options={HEADERS.flatMap((h) => (h.column ? [{ key: h.column, label: h.label }] : []))}
      sort={{ key: sort.column, direction: sort.descending ? 'descending' : 'ascending' }}
      onChange={(next) => {
        if (next) props.onChange({ column: next.key, descending: next.direction === 'descending' });
      }}
      startWith={(key) => (key === 'updated' ? 'descending' : 'ascending')}
    />
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
  const wide = useMinWidth(WIDE_TABLE_MIN);
  return (
    <>
      {wide ? null : <QueueSort sort={sort} onChange={setSort} />}
      <table className="capping-table">
        <caption>Capping queue: every proposal and where the gate has it</caption>
        <thead>
          <tr>
            {HEADERS.map(({ label, column }) =>
              column === null ? (
                <th key={label} scope="col">
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
    </>
  );
}
