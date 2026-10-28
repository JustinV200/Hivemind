/**
 * `IdToken`: an id in the monospace token the design asks for, cut to the part that tells ids apart.
 *
 * Ids appear in every view (an episode, a task, an Alarm, a bee). A real id is `<kind>_<ULID>`
 * (`waggle.ids`), and a ULID's first characters are its timestamp, so every id minted the same
 * day starts alike: cut to its head, every Cell would read `cell_01H…`. The token keeps the kind
 * and shows the ULID's tail, `cell_…2C3D4`, which is the part that differs; a short id (a fixture's
 * `cell_1`, a `worker_12`) is shown whole. The whole id is always in the `title` and as the
 * element's accessible text, so it can be read, selected and quoted to the operator in full.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by every view that names an id.
 *
 * Key invariants:
 *   - The visible text is the id's kind, an ellipsis and its last `TAIL` characters, or the id
 *     itself when it is short; the full id is always in `title` and `aria-label`.
 *   - Holds no state and reads no slice.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 6.
 *   - packages/waggle/src/waggle/ids.py for the id shape.
 */
import type { ReactElement } from 'react';

const TAIL = 5; // The ULID's random part is its last 16 characters; five of them tell ids apart.
const WHOLE = 12; // An id this short (`worker_12`, `warden_lap`) is clearer whole than cut.
const KIND_MAX = 8; // A kind prefix longer than this is not one of ours and is cut away too.

// `cell_…2C3D4`: the kind before the first underscore, when it is short, then the tail.
export function idText(id: string): string {
  if (id.length <= WHOLE) return id;
  const underscore = id.indexOf('_');
  const kind = underscore > 0 && underscore < KIND_MAX ? id.slice(0, underscore + 1) : '';
  return `${kind}…${id.slice(-TAIL)}`;
}

export function IdToken(props: { id: string }): ReactElement {
  return (
    <code className="id-token" title={props.id} aria-label={props.id}>
      {idText(props.id)}
    </code>
  );
}
