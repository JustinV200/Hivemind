/**
 * `IdToken`: an id in the monospace token the design asks for.
 *
 * Ids appear in every view (an episode, a task, an Alarm, a bee). They are shown in monospace and
 * cut to eight characters, with the whole id in the `title` and as the element's accessible text,
 * so it can be read, selected and quoted to the operator in full.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by the Thoughts view; every later view
 *   that names an id uses it too.
 *
 * Key invariants:
 *   - The visible text is a prefix of the id; the full id is always in `title` and `aria-label`.
 *   - Holds no state and reads no slice.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 6.
 */
import type { ReactElement } from 'react';

const SHOWN = 8;

export function IdToken(props: { id: string }): ReactElement {
  const cut = props.id.length > SHOWN;
  return (
    <code className="id-token" title={props.id} aria-label={props.id}>
      {cut ? `${props.id.slice(0, SHOWN)}…` : props.id}
    </code>
  );
}
