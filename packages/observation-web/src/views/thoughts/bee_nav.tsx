/**
 * `BeePicker` and `BeeLinks`: choose whose thoughts to read, and reach the bee's Cell and Attendant.
 *
 * The nav lands on the Queen's thoughts, and another bee's were reachable only through a Fleet
 * row or a Cell page. The picker is a `Bee` select in the title row, built from the Fleet slice
 * (the Queen, then each Cell's Warden and the bees whose thoughts it lists, named with their
 * Cell), and choosing one navigates to that bee's route. The links row under the title says where
 * else the bee can be read: its Cell page, and the Attendant view of its Warden (or of the Queen).
 * The fleet is read once here when no view has seeded it yet, so the picker is filled on the
 * first visit; until then, and for a bee the fleet does not list, the select names only the bee
 * that is open.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Drawn by `thoughts_view.tsx`; reads the
 *   fleet slice and seeds it through the Fleet's `useFleetSeed`.
 *
 * Key invariants:
 *   - Navigation only: choosing a bee sets the hash, which the router resolves; nothing is written.
 *   - The select always holds the open bee as an option, so its value is never blank.
 *   - A link is drawn only where the fleet names the Cell or Warden; nothing is guessed.
 *
 * See Also:
 *   - docs/observation/design.md section 5.4.
 *   - src/views/fleet/use_fleet_seed.ts for the one-off fleet read.
 */
import type { ReactElement } from 'react';
import { hrefFor } from '../../router';
import { useSlice } from '../../store';
import { useFleetSeed } from '../fleet';
import type { FleetRow } from '../fleet/slice';
import { QUEEN } from './slice';

// Where a bee lives, as the fleet tells it: its Cell, the Cell's name and the Cell's Warden.
interface Home {
  bee: string;
  cell: string;
  cellName: string;
  warden: string | null;
}

// Every bee the fleet names, in Cell order: the Warden first, then the thoughts it lists.
export function homesOf(rows: readonly FleetRow[]): Home[] {
  const homes: Home[] = [];
  for (const row of rows) {
    const bees = [row.warden_id, ...row.links.thought_principals].filter(
      (bee): bee is string => bee !== null,
    );
    for (const bee of new Set(bees))
      homes.push({ bee, cell: row.id, cellName: row.name, warden: row.warden_id });
  }
  return homes;
}

export function BeePicker(props: { bee: string }): ReactElement {
  const { rows } = useSlice('fleet');
  useFleetSeed();
  const homes = homesOf(rows);
  // The open bee stays an option even when the fleet does not list it, so the select names it.
  const listed = props.bee === QUEEN || homes.some((home) => home.bee === props.bee);
  return (
    <label className="bee-picker">
      Bee
      <select
        className="select"
        value={props.bee}
        onChange={(event) => {
          window.location.hash = hrefFor({ name: 'thoughts', bee: event.target.value });
        }}
      >
        <option value={QUEEN}>Queen</option>
        {homes.map((home) => (
          <option key={home.bee} value={home.bee}>
            {`${home.bee} (${home.cellName})`}
          </option>
        ))}
        {listed ? null : <option value={props.bee}>{props.bee}</option>}
      </select>
    </label>
  );
}

export function BeeLinks(props: { bee: string }): ReactElement | null {
  const { rows } = useSlice('fleet');
  if (props.bee === QUEEN) {
    return (
      <p className="bee-links">
        <a href={hrefFor({ name: 'attendant', principal: QUEEN })}>Attendant of the Queen</a>
      </p>
    );
  }
  const home = homesOf(rows).find((candidate) => candidate.bee === props.bee);
  if (!home) return null;
  return (
    <p className="bee-links">
      <a href={hrefFor({ name: 'cell', id: home.cell })}>{`Cell page of ${home.cellName}`}</a>
      {home.warden ? (
        <a href={hrefFor({ name: 'attendant', principal: home.warden })}>
          {`Attendant of ${home.warden}`}
        </a>
      ) : null}
    </p>
  );
}
