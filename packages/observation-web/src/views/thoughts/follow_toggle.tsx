/**
 * `FollowToggle`: the Follow switch and the "N new" button that goes with it.
 *
 * Follow on, new episodes join the top of the list as they arrive. Follow off, they wait, and this
 * button says how many and reveals them on a click, so the list the reader is looking at never
 * shifts under them. Both controls are navigation, not writes (design.md section 1).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Drawn in the title row by `thoughts_view.tsx`.
 *
 * Key invariants:
 *   - The switch is a real toggle button (`aria-pressed`) whose label carries its state ("Follow:
 *     on"), and the count is in the "N new" button's name, so neither relies on colour.
 *   - The "N new" button exists only while there is something queued and Follow is off.
 *
 * See Also:
 *   - docs/observation/design.md sections 5.4 and 7.
 */
import type { ReactElement } from 'react';

export interface FollowToggleProps {
  follow: boolean;
  queued: number;
  onFollow: (on: boolean) => void;
  onReveal: () => void;
}

export function FollowToggle(props: FollowToggleProps): ReactElement {
  const { follow, queued } = props;
  return (
    <div className="follow-toggle">
      <button
        type="button"
        className="button"
        aria-pressed={follow}
        onClick={() => props.onFollow(!follow)}
      >
        {`Follow: ${follow ? 'on' : 'off'}`}
      </button>
      {!follow && queued > 0 ? (
        <button type="button" className="button follow-new" onClick={props.onReveal}>
          {queued} new
        </button>
      ) : null}
    </div>
  );
}
