/**
 * `AuditsTable`: the sampled audits, newest first, each with the judge's finding.
 *
 * A tier the gate cannot check in real time is audited afterwards, at a sampling rate per tier, by
 * the judge (codingrules 8.12). Each row is one audit: the proposal, its tier, the finding
 * (`APPROVE`, `REQUEST CHANGES`, `REJECT`, or "not scored" when the judge never answered), the bee
 * that proposed it (a link to its thoughts), its task and when the audit was recorded. A rejected
 * finding is marked with the alarm token and the word. Under 1280 px each audit is a stacked card.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/audits/`. Drawn by `capping_screen.tsx` from the
 *   slice's audits.
 *
 * Key invariants:
 *   - Read-only and stateless: it draws its props, in the order the slice holds them.
 *   - Rows are keyed by proposal and time, since a proposal can be audited more than once.
 *   - A finding is a word as well as colour.
 *
 * See Also:
 *   - docs/observation/design.md section 5.9.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import { BeeLink } from '../queue/links';
import { NONE } from '../queue/rows';
import { wordsOf } from '../slice';
import type { AuditRow } from '../slice';

function Finding(props: { audit: AuditRow }): ReactElement {
  const { outcome, judge_error: silent } = props.audit;
  if (outcome === null) {
    return (
      <span className="capping-muted">
        {silent ? 'not scored, the judge could not answer' : 'not scored'}
      </span>
    );
  }
  const tone = outcome === 'REJECT' ? 'alarm' : outcome === 'APPROVE' ? 'ok' : 'warn';
  return <span className={`tag tag--status tag--${tone}`}>{wordsOf(outcome)}</span>;
}

export function AuditsTable(props: { audits: readonly AuditRow[] }): ReactElement {
  return (
    <table className="capping-table capping-table--plain">
      <caption>Sampled audits: the judge's second look at finished proposals</caption>
      <thead>
        <tr>
          {['Proposal', 'Tier', 'Finding', 'Bee', 'Task', 'Audited'].map((label) => (
            <th key={label} scope="col">
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {props.audits.map((audit) => (
          <tr key={`${audit.proposal_id}:${audit.at}`}>
            <td data-label="Proposal">
              <IdToken id={audit.proposal_id} />
            </td>
            <td data-label="Tier">{wordsOf(audit.tier)}</td>
            <td data-label="Finding">
              <Finding audit={audit} />
            </td>
            <td data-label="Bee">
              <BeeLink bee={audit.bee_id} />
            </td>
            <td data-label="Task">{audit.task_id ? <IdToken id={audit.task_id} /> : NONE}</td>
            <td data-label="Audited">
              <TimeStamp at={audit.at} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
