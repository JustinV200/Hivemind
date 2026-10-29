/**
 * `CappingPanel`: Capping on this Cell: proposals in flight, verdicts with reasons, rollbacks.
 *
 * Capping is the gate every action passes: a bee proposes, the gate checks, and only a capped
 * proposal is applied. The panel lists what is in flight, the verdicts the gate gave (`CAPPED`, or
 * `REJECTED` with the check that failed and whether the judge could not answer), the rollbacks
 * (with how the Cell was restored) and the proposals that finished. An Exoskeleton action (a typed
 * GUI step) also gets a muted "recording not available" note: the page carries no recording link
 * yet, so there is no playback control to offer and none is drawn.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx` from the page; the
 *   lists are refreshed by the page's trail-triggered re-read.
 *
 * Key invariants:
 *   - Each list shows at most `SHOWN` rows and says how many more there are; the page is newest
 *     first, so the newest are kept.
 *   - An Exoskeleton action is a proposal whose applied action kind is `GUI`; a verdict is matched
 *     to its proposal by id to find out.
 *   - Read-only: no control here writes or plays anything.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2 (flight-recorder playback, a later addition).
 */
import type { ReactElement } from 'react';
import { IdToken, proposalStateWords, riskTierWords, TimeStamp } from '../../../components';
import type { CellPage } from '../slice';
import { Panel, Tag } from './panel';
import type { TagTone } from './panel';

type Proposal = CellPage['capping']['in_flight'][number];
type Verdict = CellPage['capping']['verdicts'][number];

// Rows kept per list; a Cell with more says how many it left out.
const SHOWN = 8;

const STATE_TONE: Readonly<Record<Proposal['state'], TagTone>> = {
  PROPOSED: 'idle',
  CHECKING: 'idle',
  CAPPED: 'ok',
  APPLIED: 'ok',
  VERIFIED: 'ok',
  REJECTED: 'alarm',
  ROLLED_BACK: 'warn',
};

function RecordingNote(): ReactElement {
  return <span className="cell-muted">recording not available</span>;
}

function ProposalRow(props: { row: Proposal; gui: boolean }): ReactElement {
  const { row, gui } = props;
  return (
    <li className="cell-row">
      <IdToken id={row.id} />
      <Tag tone={STATE_TONE[row.state]} title={row.state}>
        {proposalStateWords(row.state)}
      </Tag>
      <span title={row.tier}>{riskTierWords(row.tier)}</span>
      <span>{`by ${row.bee_id}`}</span>
      {row.action_kind ? <span>{`action ${row.action_kind}`}</span> : null}
      {row.failing_check ? <span>{`failed check ${row.failing_check}`}</span> : null}
      {row.rollback_method ? <span>{`restored by ${row.rollback_method}`}</span> : null}
      <TimeStamp at={row.updated_at} />
      {gui ? <RecordingNote /> : null}
    </li>
  );
}

function VerdictRow(props: { verdict: Verdict; gui: boolean }): ReactElement {
  const { verdict, gui } = props;
  return (
    <li className="cell-row">
      <Tag tone={verdict.verdict === 'CAPPED' ? 'ok' : 'alarm'} title={verdict.verdict}>
        {proposalStateWords(verdict.verdict)}
      </Tag>
      <IdToken id={verdict.proposal_id} />
      <span title={verdict.tier}>{riskTierWords(verdict.tier)}</span>
      <span>{`${verdict.checks_run} checks run`}</span>
      {verdict.failing_check ? <span>{`failed check ${verdict.failing_check}`}</span> : null}
      {verdict.judge_error ? <span>the judge could not answer</span> : null}
      <TimeStamp at={verdict.at} />
      {gui ? <RecordingNote /> : null}
    </li>
  );
}

function More(props: { total: number }): ReactElement | null {
  const left = props.total - SHOWN;
  return left > 0 ? <p className="cell-muted">{`and ${left} more`}</p> : null;
}

function ProposalList(props: {
  title: string;
  empty: string;
  rows: readonly Proposal[];
  gui: ReadonlySet<string>;
}): ReactElement {
  const { title, empty, rows, gui } = props;
  return (
    <>
      <h3>{title}</h3>
      {rows.length === 0 ? <p>{empty}</p> : null}
      <ul className="cell-list">
        {rows.slice(0, SHOWN).map((row) => (
          <ProposalRow key={row.id} row={row} gui={gui.has(row.id)} />
        ))}
      </ul>
      <More total={rows.length} />
    </>
  );
}

export function CappingPanel(props: { page: CellPage }): ReactElement {
  const { capping } = props.page;
  const everyProposal = [...capping.in_flight, ...capping.finished, ...capping.rollbacks];
  // Proposals whose applied action was a typed GUI step: the Exoskeleton's.
  const gui = new Set(everyProposal.filter((p) => p.action_kind === 'GUI').map((p) => p.id));
  return (
    <Panel id="capping" title="Capping">
      <ProposalList
        title="In flight"
        empty="No proposals waiting on the gate."
        rows={capping.in_flight}
        gui={gui}
      />
      <h3>Verdicts</h3>
      {capping.verdicts.length === 0 ? <p>No verdicts yet.</p> : null}
      <ul className="cell-list">
        {capping.verdicts.slice(0, SHOWN).map((verdict) => (
          <VerdictRow
            key={`${verdict.proposal_id}:${verdict.at}`}
            verdict={verdict}
            gui={gui.has(verdict.proposal_id)}
          />
        ))}
      </ul>
      <More total={capping.verdicts.length} />
      <ProposalList
        title="Rollbacks"
        empty="Nothing rolled back."
        rows={capping.rollbacks}
        gui={gui}
      />
      <ProposalList
        title="Finished"
        empty="Nothing finished yet."
        rows={capping.finished}
        gui={gui}
      />
    </Panel>
  );
}
