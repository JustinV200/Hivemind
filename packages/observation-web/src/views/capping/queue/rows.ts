/**
 * The queue's rows: each proposal joined to its verdict, then filtered and sorted.
 *
 * A row is a proposal, the gate's newest verdict on it (if it has one) and whether it was an
 * Exoskeleton action. The Cell page treats an applied action of kind `GUI` as one (a typed step
 * on a screen), so this view does the same. The reason shown for a verdict is what the schema
 * carries: the failing check as a code, whether the judge could not answer, and how many checks
 * ran; the reviewer's prose is not on the trail and so not here. Filtering takes sets of tiers
 * and states (an empty set lets everything through); sorting is stable and ties break on the
 * proposal's id, so the order is total.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/queue/`. Built by `capping_screen.tsx` and drawn
 *   by `queue_table.tsx`; pure so the join, the filter and the order are tested without rendering.
 *
 * Key invariants:
 *   - One row per proposal in the slice; a verdict with no proposal row in view is not drawn.
 *   - The newest verdict of a proposal is the one shown.
 *   - A missing value is the hyphen `-`, never an empty cell.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 5.9.
 */
import { stateRank, tierRank } from '../slice';
import type { CappingState, ProposalRow, ProposalState, VerdictRow } from '../slice';

export const NONE = '-';

// The applied action kind of an Exoskeleton step, as the Cell page reads it.
const EXOSKELETON_KIND = 'GUI';

export interface QueueRow {
  proposal: ProposalRow;
  verdict: VerdictRow | null;
  exoskeleton: boolean;
}

export type QueueColumn = 'updated' | 'tier' | 'state' | 'bee' | 'task' | 'cell' | 'verdict';

export function queueRows(state: Pick<CappingState, 'proposals' | 'verdicts'>): QueueRow[] {
  // Verdicts arrive newest first, so the first one seen for a proposal is its newest.
  const newest = new Map<string, VerdictRow>();
  for (const verdict of state.verdicts) {
    if (!newest.has(verdict.proposal_id)) newest.set(verdict.proposal_id, verdict);
  }
  return state.proposals.map((proposal) => ({
    proposal,
    verdict: newest.get(proposal.id) ?? null,
    exoskeleton: proposal.action_kind === EXOSKELETON_KIND,
  }));
}

export function filterRows(
  rows: readonly QueueRow[],
  tiers: ReadonlySet<string>,
  states: ReadonlySet<ProposalState>,
): QueueRow[] {
  return rows.filter(
    ({ proposal }) =>
      (tiers.size === 0 || tiers.has(proposal.tier)) &&
      (states.size === 0 || states.has(proposal.state)),
  );
}

// Why the gate said what it said, in the schema's own terms; the hyphen when it has said nothing.
export function reasonOf(row: QueueRow): string {
  const { proposal, verdict } = row;
  const check = verdict?.failing_check ?? proposal.failing_check;
  const parts: string[] = [];
  if (check) parts.push(`failed check ${check}`);
  if (verdict?.judge_error) parts.push('the judge could not answer');
  if (verdict?.verdict === 'CAPPED') parts.push(`${verdict.checks_run} checks passed`);
  return parts.length > 0 ? parts.join(', ') : NONE;
}

// How a rolled-back proposal was restored, or the hyphen for one that was not rolled back.
export function rollbackOf(proposal: ProposalRow): string {
  if (proposal.state !== 'ROLLED_BACK') return NONE;
  return proposal.rollback_method
    ? `rolled back, restored by ${proposal.rollback_method}`
    : 'rolled back';
}

function keyOf(row: QueueRow, column: QueueColumn): number | string {
  const { proposal, verdict } = row;
  switch (column) {
    case 'updated':
      return Date.parse(proposal.updated_at);
    case 'tier':
      return tierRank(proposal.tier);
    case 'state':
      return stateRank(proposal.state);
    case 'bee':
      return proposal.bee_id;
    case 'task':
      return proposal.task_id ?? '';
    case 'cell':
      return proposal.cell_id ?? '';
    case 'verdict':
      return verdict?.verdict ?? '';
  }
}

// Sorts by one column in the given direction; ties break on the proposal id, ascending.
export function sortRows(
  rows: readonly QueueRow[],
  column: QueueColumn,
  descending: boolean,
): QueueRow[] {
  const sign = descending ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = keyOf(a, column);
    const y = keyOf(b, column);
    const order = x < y ? -1 : x > y ? 1 : 0;
    return (
      order * sign || (a.proposal.id < b.proposal.id ? -1 : a.proposal.id > b.proposal.id ? 1 : 0)
    );
  });
}
