/**
 * `useTarget(step)`: the element a tour step outlines, found on the page once its screen is drawn,
 * or reported absent so the tour can skip the step.
 *
 * A step on another screen names a route; the hook opens it and then looks for the element,
 * retrying briefly, because the view draws on the next render and may need a moment. The old
 * screen's elements are still on the page when the route changes, so an element found before the
 * new screen replaced it is let go the moment it leaves the page (a `MutationObserver` watches)
 * and looked for again. An element the stylesheet hides (a nav item behind More on a phone) counts
 * as absent where the browser can tell (`checkVisibility`); where it cannot (jsdom), any element
 * on the page counts, so the tests see every step. After a short while with nothing found the step
 * is absent, and the panel moves on.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/tour/`. Used by `tour.tsx`.
 *
 * Key invariants:
 *   - Looks only by `data-tour`, never by class or position.
 *   - The timer and the observer are cleared when the step changes or the panel closes.
 *   - Sets the hash only when it differs, so a step on the open screen changes nothing.
 *
 * See Also:
 *   - src/views/help/tour/steps.ts for the targets.
 */
import { useEffect, useState } from 'react';
import type { TourStep } from './steps';

export interface Target {
  element: HTMLElement | null;
  // True once the element was not found within the retries: the step has nothing to outline.
  absent: boolean;
}

const LOOKING: Target = { element: null, absent: false };
const TRY_MS = 50; // A view draws within a render or two; this keeps the retries fine-grained.
const MAX_TRIES = 20; // One second in all, more than any screen needs to draw its title row.

// Shown where the browser can say; present counts as shown where it cannot (jsdom).
function isShown(element: HTMLElement): boolean {
  return typeof element.checkVisibility === 'function' ? element.checkVisibility() : true;
}

// Looks for the step's element until it is found or the retries run out, reporting each outcome,
// and returns what stops the looking.
function watch(step: TourStep, report: (target: Target) => void): () => void {
  let element: HTMLElement | null = null;
  let tries = 0;
  let timer = 0;
  let gaveUp = false;

  // One look at the page; a miss tries again after a short wait, up to the limit.
  const look = (): void => {
    const candidate = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
    if (candidate !== null && isShown(candidate)) {
      element = candidate;
      report({ element, absent: false });
      return;
    }
    tries += 1;
    if (tries >= MAX_TRIES) {
      gaveUp = true;
      report({ element: null, absent: true });
      return;
    }
    timer = window.setTimeout(look, TRY_MS);
  };

  // The screen a route opens replaces the old one's elements, so an element found before the
  // route settled is dropped once it leaves the page, and the look starts over.
  const observer = new MutationObserver(() => {
    if (!gaveUp && element !== null && !element.isConnected) {
      element = null;
      look();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  look();
  return () => {
    observer.disconnect();
    window.clearTimeout(timer);
  };
}

export function useTarget(step: TourStep): Target {
  // What was found, and for which step: a result for another step reads as still looking, so
  // a step change needs no reset of its own.
  const [found, setFound] = useState<{ step: TourStep; target: Target } | null>(null);

  useEffect(() => {
    if (step.route !== undefined && window.location.hash !== step.route) {
      window.location.hash = step.route;
    }
    return watch(step, (target) => setFound({ step, target }));
  }, [step]);

  return found?.step === step ? found.target : LOOKING;
}
