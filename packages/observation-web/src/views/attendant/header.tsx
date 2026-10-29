/**
 * `AttendantHeader`: the title row, who the view is for, and where else to read that principal.
 *
 * The heading names the principal (`queen` or a Warden's id). Under it, the title row carries the
 * view's one-line gloss, the same sentence the nav item carries (design.md section 2), and a line
 * links to the principal's thoughts. For a Warden it also links to the Warden's Cell page, when
 * one of the Warden's own tasks says which Cell it is placed on: the Attendant view does not name
 * the Warden's Cell by itself, so no task placed there means no link, never a guess.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/`. Drawn by `attendant.tsx`.
 *
 * Key invariants:
 *   - The title and the principal show at once, before any read has answered.
 *   - Links are built with the router's `hrefFor`, so they match the routes the shell answers.
 *
 * See Also:
 *   - docs/observation/design.md section 5.5.
 */
import type { ReactElement } from 'react';
import { VIEW_HINTS, ViewTitle } from '../../components';
import { hrefFor } from '../../router';
import type { TaskNode } from './slice';

export const QUEEN = 'queen';

// The Cell a Warden's own tasks are placed on, if any task of its says so.
export function cellOfWarden(principal: string, nodes: readonly TaskNode[]): string | null {
  const own = nodes.find((node) => node.warden_id === principal && node.cell_id !== null);
  return own?.cell_id ?? null;
}

export function AttendantHeader(props: {
  principal: string;
  nodes: readonly TaskNode[];
}): ReactElement {
  const { principal, nodes } = props;
  const queen = principal === QUEEN;
  const cell = queen ? null : cellOfWarden(principal, nodes);
  return (
    <header className="att-header">
      <ViewTitle
        title="Attendant"
        subject={principal}
        hint={VIEW_HINTS.attendant}
        help="attendant"
      />
      <p className="att-links">
        <a href={hrefFor({ name: 'thoughts', bee: principal })}>{`Thoughts of ${principal}`}</a>
        {cell ? <a href={hrefFor({ name: 'cell', id: cell })}>{`Cell page of ${cell}`}</a> : null}
      </p>
    </header>
  );
}
