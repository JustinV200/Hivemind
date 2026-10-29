/**
 * `TourCard`: the card on the Help screen that starts the tour and says whether it was taken.
 *
 * One heading, one line and one button. The line says what the tour is or, once this browser has
 * finished it, that it has, so the button's words change too; the tour itself is drawn by the
 * shell, not here, since it moves between screens.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/tour/`. Drawn by `help_screen.tsx`; starts the tour
 *   through `state.ts`.
 *
 * Key invariants:
 *   - The only way the tour starts is this button (or `startTour` in a test).
 *   - Reads the finished mark on each render, so finishing the tour updates the words at once.
 *
 * See Also:
 *   - src/views/help/tour/tour.tsx for the panel.
 */
import type { ReactElement } from 'react';
import { startTour, tourDone, useTour } from './state';

// The button's id, so the panel can hand focus back to it when the tour ends on this screen.
export const TOUR_START_ID = 'help-tour-start';

export function TourCard(): ReactElement {
  // Subscribed so the card redraws when the tour closes, which is when the mark can change.
  useTour();
  const done = tourDone();
  return (
    <section className="help-tour comb-cell" aria-labelledby="help-tour-title">
      <h2 id="help-tour-title">The tour</h2>
      <p>
        {done
          ? 'You finished the tour on this browser. Take it again any time.'
          : 'A step-by-step walk through the shell and the main screens, each part outlined ' +
            'on the page.'}
      </p>
      <button type="button" id={TOUR_START_ID} className="button" onClick={startTour}>
        {done ? 'Take the tour again' : 'Take the tour'}
      </button>
    </section>
  );
}
