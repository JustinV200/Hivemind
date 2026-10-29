/**
 * `Tour`: the guided tour's panel and the ring it draws around the step's element.
 *
 * The panel is a small dialog fixed at the bottom of the page (above the nav on a phone, at the
 * right from tablet width) with the step's title, its words, `Step N of M` in words, and Back,
 * Next (Finish on the last step) and Skip tour. It is not modal: the page stays usable around it,
 * and nothing is dimmed. The element the step is about is outlined by a ring drawn at its measured
 * box (kept inside the viewport), re-measured on scroll and resize, and scrolled into view
 * (without motion where the person asked for none); the panel's words name the element too, so
 * the ring is never the only signal. When the element sits in the lower half of the viewport the
 * panel moves to the top, under the head, so it never covers what it is talking about.
 * Focus moves to the panel on each step, so a keyboard reader hears the step and reaches its
 * buttons by Tab; the arrow keys move and Escape skips. When the tour ends, focus goes back to
 * the Help card's button if that card is on the page, so a keyboard user is not dropped on the
 * body. A step whose element is absent is skipped the way the tour was moving.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/tour/`. Mounted once by `src/shell/shell.tsx`, so
 *   it outlives the route changes its steps make; opened by `tour_card.tsx` on the Help screen.
 *
 * Key invariants:
 *   - Draws nothing while the tour is closed, and never opens on its own: unmounting (a log out
 *     or a lost session mid-tour) closes it, so the next session never opens on an old step.
 *   - The steps are a prop with the real table as its default, so a test can render its own.
 *   - Reads the tour's position from `state.ts` only; the route is changed by `use_target.ts`.
 *
 * See Also:
 *   - docs/observation/design.md sections 5.12 and 7.
 */
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, ReactElement } from 'react';
import { closeTour, moveTour, skipTour, useTour } from './state';
import { TOUR_STEPS } from './steps';
import type { TourStep } from './steps';
import './tour.css';
import { TOUR_START_ID } from './tour_card';
import { useTarget } from './use_target';

// Space between the element's box and the ring, so the ring never covers its edge.
const RING_GAP = 4;
// The ring's least distance from the viewport's edge: a full-width element (the strip, the nav)
// would otherwise put the ring's sides off screen.
const EDGE_GAP = 2;

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

// The element's box on the viewport, kept current while the page scrolls or resizes; a box
// measured for another element reads as none, so a changed element needs no reset.
function useRect(element: HTMLElement | null): Rect | null {
  const [measured, setMeasured] = useState<{ element: HTMLElement; rect: Rect } | null>(null);
  useEffect(() => {
    if (element === null) return;
    const measure = (): void => {
      const box = element.getBoundingClientRect();
      setMeasured({
        element,
        rect: { top: box.top, left: box.left, width: box.width, height: box.height },
      });
    };
    // jsdom has no scrollIntoView; a browser brings the element to the middle of the viewport.
    if (typeof element.scrollIntoView === 'function') {
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
      element.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' });
    }
    measure();
    // Capture, since the rail and a panel scroll on their own, not only the page.
    window.addEventListener('scroll', measure, { capture: true, passive: true });
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, { capture: true });
      window.removeEventListener('resize', measure);
    };
  }, [element]);
  return measured?.element === element ? measured.rect : null;
}

// The outline around the step's element, a little outside its box; decorative, since the panel
// names the element in words.
function Ring(props: { rect: Rect }): ReactElement {
  const { rect } = props;
  const top = Math.max(rect.top - RING_GAP, EDGE_GAP);
  const left = Math.max(rect.left - RING_GAP, EDGE_GAP);
  const bottom = Math.min(rect.top + rect.height + RING_GAP, window.innerHeight - EDGE_GAP);
  const right = Math.min(rect.left + rect.width + RING_GAP, window.innerWidth - EDGE_GAP);
  return (
    <div
      className="tour-ring"
      aria-hidden="true"
      style={{ top, left, width: right - left, height: bottom - top }}
    />
  );
}

// Whether the element sits in the lower half of the viewport, where the panel would cover it.
function isLow(rect: Rect | null): boolean {
  return rect !== null && rect.top + rect.height / 2 > window.innerHeight / 2;
}

function Buttons(props: { index: number; count: number }): ReactElement {
  const { index, count } = props;
  return (
    <div className="tour-row">
      <button
        type="button"
        className="button"
        disabled={index === 0}
        onClick={() => moveTour(-1, count)}
      >
        Back
      </button>
      <button type="button" className="button" onClick={() => moveTour(1, count)}>
        {index + 1 === count ? 'Finish' : 'Next'}
      </button>
      <button type="button" className="button tour-skip" onClick={closeTour}>
        Skip tour
      </button>
    </div>
  );
}

function Panel(props: { index: number; steps: readonly TourStep[] }): ReactElement {
  const { index, steps } = props;
  const step = steps[index]!;
  const count = steps.length;
  const target = useTarget(step);
  const rect = useRect(target.element);
  const panel = useRef<HTMLElement>(null);

  // The step changed: say it, and put its buttons next under Tab.
  useEffect(() => {
    panel.current?.focus({ preventScroll: true });
  }, [index]);

  // The tour ended (Finish, Skip or Escape): hand focus back to the card's button when the Help
  // screen is open, so a keyboard user is placed where the tour began.
  useEffect(() => () => document.getElementById(TOUR_START_ID)?.focus(), []);

  // Nothing to outline on this screen: move on the way the tour was going.
  useEffect(() => {
    if (target.absent) skipTour(count);
  }, [target.absent, count]);

  const onKey = (event: KeyboardEvent): void => {
    if (event.key === 'Escape') closeTour();
    else if (event.key === 'ArrowRight') moveTour(1, count);
    else if (event.key === 'ArrowLeft') moveTour(-1, count);
    else return;
    event.preventDefault();
  };

  return (
    <>
      {rect ? <Ring rect={rect} /> : null}
      <section
        className={`tour comb-cell comb-cell--strong${isLow(rect) ? ' tour--top' : ''}`}
        role="dialog"
        aria-labelledby="tour-title"
        aria-describedby="tour-progress tour-text"
        tabIndex={-1}
        ref={panel}
        onKeyDown={onKey}
      >
        <p id="tour-progress" className="tour-progress">{`Step ${index + 1} of ${count}`}</p>
        <h2 id="tour-title">{step.title}</h2>
        <p id="tour-text">{step.text}</p>
        <Buttons index={index} count={count} />
      </section>
    </>
  );
}

export function Tour(props: { steps?: readonly TourStep[] }): ReactElement | null {
  const { step } = useTour();
  const steps = props.steps ?? TOUR_STEPS;

  // The position is module state, so it outlives the shell: a log out or a lost session mid-tour
  // must not reopen the panel, and move the route, when the next session mounts the shell.
  useEffect(() => closeTour, []);
  if (step === null || step >= steps.length) return null;
  return <Panel index={step} steps={steps} />;
}
