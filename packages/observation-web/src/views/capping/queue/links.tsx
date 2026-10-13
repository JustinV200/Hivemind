/**
 * The small links and notes a queue row and an audit row share.
 *
 * Every item links to the thoughts of the bee that proposed it, and to the Cell page when the
 * schema names a Cell. For an Exoskeleton action the design asks for a link to the flight
 * recording, but the Capping schema carries no recording reference yet (the Entrance reads no
 * recording store), so a muted note says so; no control is drawn that would lead nowhere.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/queue/`. Used by the queue and audit tables.
 *
 * Key invariants:
 *   - Links are plain anchors to hash routes built by `hrefFor`, so they work with the router and
 *     from a pasted URL.
 *   - The recording note is text, never a button or a link.
 *
 * See Also:
 *   - docs/observation/design.md section 5.9, src/views/cell/panels/capping_panel.tsx.
 */
import type { ReactElement } from 'react';
import { IdToken } from '../../../components';
import { hrefFor } from '../../../router';

export function BeeLink(props: { bee: string }): ReactElement {
  return (
    <a href={hrefFor({ name: 'thoughts', bee: props.bee })} aria-label={`thoughts of ${props.bee}`}>
      <IdToken id={props.bee} />
    </a>
  );
}

export function CellLink(props: { cell: string }): ReactElement {
  return (
    <a href={hrefFor({ name: 'cell', id: props.cell })} aria-label={`Cell page of ${props.cell}`}>
      <IdToken id={props.cell} />
    </a>
  );
}

export function RecordingNote(): ReactElement {
  return <span className="capping-muted">recording not available</span>;
}
