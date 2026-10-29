/**
 * `QueueFilters`: the tier filter and the state filter in the Capping view's title row.
 *
 * From the rail width each group is a row of chips. A chip is a toggle button; a group has an
 * "All" chip that is pressed while no chip of that group is, and pressing it clears the group.
 * Several chips of one group can be on at once (a proposal matches when its tier is among the
 * chosen tiers and its state among the chosen states). Each chip shows how many rows of the queue
 * it would keep, so an empty choice is never a surprise. On a phone the two groups would wrap to
 * six rows before any content, so there each group is one select naming one tier or state (or
 * All) with the same counts; it drives the same sets, one member at a time, and a set of several
 * (chosen by chips before the window narrowed) collapses to its first member when the select
 * takes over, so the control always says what the table shows. Filters change what is drawn and
 * nothing else; they never change what is read.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/queue/`. Drawn by `capping_screen.tsx`; the
 *   chosen sets live in the screen (`useQueueFilter`) so the table and the chips agree.
 *
 * Key invariants:
 *   - Every chip has a visible word as its label and `aria-pressed` for its state; a select has
 *     its group's name as its label.
 *   - The state chips always list the gate's seven states in lifecycle order, so they never move;
 *     the tier chips list the tiers present, least dangerous first.
 *
 * See Also:
 *   - docs/observation/design.md section 5.9.
 */
import { useCallback, useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { proposalStateWords, RAIL_MIN, riskTierWords, useMinWidth } from '../../../components';
import { STATES, tierRank } from '../slice';
import type { ProposalState } from '../slice';
import type { QueueRow } from './rows';

export interface QueueFilter {
  tiers: ReadonlySet<string>;
  states: ReadonlySet<ProposalState>;
  // A chip adds or removes one member; null (the "All" chip) empties the group.
  toggleTier: (tier: string | null) => void;
  toggleState: (state: ProposalState | null) => void;
  // A select makes the group that one member; null (its "All" option) empties it.
  chooseTier: (tier: string | null) => void;
  chooseState: (state: ProposalState | null) => void;
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
  const chooseTier = useCallback((tier: string | null) => setTiers(only(tier)), []);
  const chooseState = useCallback((state: ProposalState | null) => setStates(only(state)), []);
  return { tiers, states, toggleTier, toggleState, chooseTier, chooseState };
}

// The set holding just the member, or the empty set for "All".
function only<T>(member: T | null): Set<T> {
  return member === null ? new Set() : new Set([member]);
}

// A chip's label is the option's words; the gate's own code rides on its title for the operator.
function Chip(props: {
  label: string;
  code?: string;
  count: number;
  on: boolean;
  onToggle: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className="capping-chip"
      title={props.code}
      aria-pressed={props.on}
      onClick={props.onToggle}
    >
      {props.label} <span className="capping-chip-count">{props.count}</span>
    </button>
  );
}

interface GroupProps<T extends string> {
  label: string;
  options: readonly T[];
  chosen: ReadonlySet<T>;
  countOf: (option: T) => number;
  total: number;
  // The option as the chip or the select names it: the tier as what it does, a state as a word.
  wordsOf: (option: T) => string;
  onToggle: (option: T | null) => void;
  onChoose: (option: T | null) => void;
}

// The phone's form of a group: one select. Several members chosen by chips before the window
// narrowed collapse to the first of them in the options' order, since a select names one and
// must name what the table is filtered to.
function SelectGroup<T extends string>(props: GroupProps<T>): ReactElement {
  const { chosen: set, options, onChoose } = props;
  useEffect(() => {
    if (set.size > 1) onChoose(options.find((option) => set.has(option)) ?? null);
  }, [set, options, onChoose]);
  const chosen = [...set];
  // The value is one of the options or the empty "All": the select itself guarantees it.
  const choose = (value: string): void => props.onChoose(value === '' ? null : (value as T));
  return (
    <label className="capping-select">
      {props.label}
      <select
        value={chosen.length === 1 ? (chosen[0] as T) : ''}
        onChange={(event) => choose(event.target.value)}
      >
        <option value="">{`All (${props.total})`}</option>
        {props.options.map((option) => (
          <option key={option} value={option}>
            {`${props.wordsOf(option)} (${props.countOf(option)})`}
          </option>
        ))}
      </select>
    </label>
  );
}

function Group<T extends string>(props: GroupProps<T>): ReactElement {
  const chips = useMinWidth(RAIL_MIN);
  if (!chips) return <SelectGroup {...props} />;
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
          label={props.wordsOf(option)}
          code={option}
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
        wordsOf={riskTierWords}
        onToggle={filter.toggleTier}
        onChoose={filter.chooseTier}
      />
      <Group
        label="Show state"
        options={STATES}
        chosen={filter.states}
        countOf={(state) => rows.filter((r) => r.proposal.state === state).length}
        total={rows.length}
        wordsOf={proposalStateWords}
        onToggle={filter.toggleState}
        onChoose={filter.chooseState}
      />
    </>
  );
}
