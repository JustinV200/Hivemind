/**
 * `QueueFilters`: the tier chips and the state chips in the Capping view's title row.
 *
 * A chip is a toggle button; a group has an "All" chip that is pressed while no chip of that group
 * is, and pressing it clears the group. Several chips of one group can be on at once (a proposal
 * matches when its tier is among the chosen tiers and its state among the chosen states). Each chip
 * shows how many rows of the queue it would keep, so an empty choice is never a surprise. Chips
 * filter what is drawn and nothing else; they never change what is read.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/queue/`. Drawn by `capping_screen.tsx`; the
 *   chosen sets live in the screen (`useQueueFilter`) so the table and the chips agree.
 *
 * Key invariants:
 *   - Every chip has a visible word as its label and `aria-pressed` for its state.
 *   - The state chips always list the gate's seven states in lifecycle order, so they never move;
 *     the tier chips list the tiers present, least dangerous first.
 *
 * See Also:
 *   - docs/observation/design.md section 5.9.
 */
import { useCallback, useState } from 'react';
import type { ReactElement } from 'react';
import { STATES, tierRank, wordsOf } from '../slice';
import type { ProposalState } from '../slice';
import type { QueueRow } from './rows';

export interface QueueFilter {
  tiers: ReadonlySet<string>;
  states: ReadonlySet<ProposalState>;
  toggleTier: (tier: string | null) => void;
  toggleState: (state: ProposalState | null) => void;
}

// Adds the member, or removes it when present; null (the "All" chip) empties the set.
function toggled<T>(set: ReadonlySet<T>, member: T | null): Set<T> {
  if (member === null) return new Set();
  const next = new Set(set);
  if (!next.delete(member)) next.add(member);
  return next;
}

export function useQueueFilter(): QueueFilter {
  const [tiers, setTiers] = useState<ReadonlySet<string>>(new Set());
  const [states, setStates] = useState<ReadonlySet<ProposalState>>(new Set());
  const toggleTier = useCallback(
    (tier: string | null) => setTiers((now) => toggled(now, tier)),
    [],
  );
  const toggleState = useCallback(
    (state: ProposalState | null) => setStates((now) => toggled(now, state)),
    [],
  );
  return { tiers, states, toggleTier, toggleState };
}

function Chip(props: {
  label: string;
  count: number;
  on: boolean;
  onToggle: () => void;
}): ReactElement {
  return (
    <button type="button" className="capping-chip" aria-pressed={props.on} onClick={props.onToggle}>
      {props.label} <span className="capping-chip-count">{props.count}</span>
    </button>
  );
}

function Group<T extends string>(props: {
  label: string;
  options: readonly T[];
  chosen: ReadonlySet<T>;
  countOf: (option: T) => number;
  total: number;
  onToggle: (option: T | null) => void;
}): ReactElement {
  return (
    <div className="capping-chips" role="group" aria-label={props.label}>
      <Chip
        label="All"
        count={props.total}
        on={props.chosen.size === 0}
        onToggle={() => props.onToggle(null)}
      />
      {props.options.map((option) => (
        <Chip
          key={option}
          label={wordsOf(option)}
          count={props.countOf(option)}
          on={props.chosen.has(option)}
          onToggle={() => props.onToggle(option)}
        />
      ))}
    </div>
  );
}

export function QueueFilters(props: {
  rows: readonly QueueRow[];
  filter: QueueFilter;
}): ReactElement {
  const { rows, filter } = props;
  // Tiers present in the queue, least dangerous first; a chosen tier stays listed if it empties.
  const tiers = [...new Set([...rows.map((r) => r.proposal.tier), ...filter.tiers])].sort(
    (a, b) => tierRank(a) - tierRank(b) || (a < b ? -1 : 1),
  );
  return (
    <>
      <Group
        label="Show tier"
        options={tiers}
        chosen={filter.tiers}
        countOf={(tier) => rows.filter((r) => r.proposal.tier === tier).length}
        total={rows.length}
        onToggle={filter.toggleTier}
      />
      <Group
        label="Show state"
        options={STATES}
        chosen={filter.states}
        countOf={(state) => rows.filter((r) => r.proposal.state === state).length}
        total={rows.length}
        onToggle={filter.toggleState}
      />
    </>
  );
}
