/**
 * The step-up banner: shown under the top bar while the session still needs a step-up.
 *
 * The travel lock sets `needs_step_up` when it sees the device on a new network, and the
 * Entrance then wants both factors again before anything else. The banner says so and points to
 * the Devices screen, where the prompt lives. It disappears when the session slice clears the
 * flag.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `shell.tsx` between the bar and the strip.
 *
 * Key invariants:
 *   - It renders nothing at all unless the session needs a step-up.
 *   - The banner is a polite status, not an alert: it is information, not a failure.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 7 for step-up.
 *   - docs/observation/design.md section 2.
 */
import type { ReactElement } from 'react';
import { useSlice } from '../store';

export function StepUpBanner(): ReactElement | null {
  const { needsStepUp } = useSlice('session');
  if (!needsStepUp) return null;
  return (
    <div className="banner" role="status">
      This device is on a new network. <a href="#/devices">Confirm it is you</a> with your password
      and your device key before anything else.
    </div>
  );
}
