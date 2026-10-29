/**
 * The Fleet row's small marks: where the Warden runs, where the models come from, Cell Wax, and
 * a sub-bee's thoughts link.
 *
 * The two place badges answer "where does the thinking happen": the Warden on the Hive Stand or
 * on the Cell, and the Cell's models local, from the Hive Stand, hosted or hybrid by its hosting
 * plan. They are neither a tier pill nor a mask tag (a quiet square tag on the comb surface), and
 * each carries its words. The Cell Wax mark is a status dot with the highest live severity as a word.
 * A sub-bee's thoughts link is a numbered comb cell: small enough that a row with three sub-bees
 * stays one line, named in full for assistive technology and on hover.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Used by `fleet_row.tsx`; styled by
 *   `fleet.css` and, for the dot, `src/styles/components.css`.
 *
 * Key invariants:
 *   - Every mark is words first; colour is never the only signal (a bee link has its number and its
 *     name).
 *   - An unknown place reads `unknown` and no Warden reads `no Warden`; nothing is guessed.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.4 and 5.1.
 */
import type { ReactElement } from 'react';
import { HEXAGON, IdToken, plainCode } from '../../../components';
import type { FleetRow } from '../slice';

const WARDEN_WORDS: Record<NonNullable<FleetRow['warden_runs']>, string> = {
  hive_stand: 'Hive Stand',
  on_cell: 'on the Cell',
};

const MODELS_WORDS: Record<FleetRow['models_from'], string> = {
  local: 'local',
  hive_stand: 'Hive Stand',
  hosted: 'hosted',
  hybrid: 'hybrid',
  unknown: 'unknown',
};

export function WardenBadge(props: { runs: FleetRow['warden_runs'] }): ReactElement {
  const label = props.runs === null ? 'no Warden' : WARDEN_WORDS[props.runs];
  return <span className={`badge badge-place badge-place--${props.runs ?? 'none'}`}>{label}</span>;
}

export function ModelsBadge(props: { from: FleetRow['models_from'] }): ReactElement {
  return (
    <span className={`badge badge-place badge-place--${props.from}`}>
      {MODELS_WORDS[props.from]}
    </span>
  );
}

// The dot's tone follows the status tokens: a note is quiet, a caution warns, a block alarms.
const WAX_TONE: Record<NonNullable<FleetRow['highest_wax_severity']>, string> = {
  NOTE: '',
  CAUTION: ' status-dot--warn',
  BLOCK: ' status-dot--alarm',
};

export function WaxMark(props: { severity: FleetRow['highest_wax_severity'] }): ReactElement {
  if (props.severity === null) return <span className="fleet-none">none</span>;
  return (
    <span className="status">
      <span className={`status-dot${WAX_TONE[props.severity]}`} aria-hidden="true" />
      <span title={props.severity}>{plainCode(props.severity)}</span>
    </span>
  );
}

// The digit's size in the cell's 24-unit box: at the 20px the stylesheet draws the cell, it lands
// at 12px, the floor design.md 4.8 sets for text in a drawing.
const DIGIT_SIZE = 14.4;

// A sub-bee's thoughts as a numbered comb cell; the bee's id is drawn beside it only on a card
// (`withId`), where there is room, and is in the link's name everywhere.
export function BeeLink(props: {
  bee: string;
  index: number;
  href: string;
  withId: boolean;
}): ReactElement {
  return (
    <a
      className="fleet-bee"
      href={props.href}
      aria-label={`Thoughts of ${props.bee}`}
      title={props.bee}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d={HEXAGON} fill="var(--surface-2)" stroke="currentColor" strokeWidth="1.5" />
        <text
          x="12"
          y="17"
          textAnchor="middle"
          fontSize={DIGIT_SIZE}
          fontWeight="600"
          fill="currentColor"
        >
          {props.index}
        </text>
      </svg>
      {props.withId ? <IdToken id={props.bee} /> : null}
    </a>
  );
}
