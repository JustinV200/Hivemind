/**
 * Where the tour is: closed, or at a step, moving forward or back; and whether this browser
 * finished it.
 *
 * The tour is started from the Help screen and drawn by the shell, which outlive each other's
 * renders, so its position lives outside React as a small external store, like the theme, and
 * each reads it through `useSyncExternalStore`. The finished mark is kept in `localStorage`, so the
 * Help screen can say the tour was taken; storage can be absent or refused (a private window), so
 * every read and write is guarded and a refusal only means the mark is not kept.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/tour/`. Read by `tour.tsx` and `tour_card.tsx`;
 *   moved by the panel's buttons and keys, and by `use_target.ts` when a step's target is absent.
 *
 * Key invariants:
 *   - The tour never starts on its own: only `startTour`, which the card's button calls, opens it.
 *   - `direction` is the way the last move went, so a step whose target is absent is skipped the
 *     same way; skipping back off the first step turns forward, so the tour never loops.
 *   - Finishing marks the browser; skipping (closing) does not.
 *
 * See Also:
 *   - src/shell/theme/choice.ts, the same shape for the theme.
 */
import { useSyncExternalStore } from 'react';

export interface TourState {
  // The open step's index, or null while the tour is closed.
  step: number | null;
  direction: 1 | -1;
}

const KEY = 'hive.tour';
const CLOSED: TourState = { step: null, direction: 1 };
let state: TourState = CLOSED;
const listeners = new Set<() => void>();

function set(next: TourState): void {
  state = next;
  listeners.forEach((wake) => wake());
}

// Whether this browser has finished the tour; false when storage cannot be read.
export function tourDone(): boolean {
  try {
    return window.localStorage.getItem(KEY) === 'done';
  } catch {
    return false;
  }
}

export function startTour(): void {
  set({ step: 0, direction: 1 });
}

// Skip: close without a mark, so the card still offers the tour next time.
export function closeTour(): void {
  set(CLOSED);
}

// Finish: the last step was passed, so the browser remembers it.
export function finishTour(): void {
  try {
    window.localStorage.setItem(KEY, 'done');
  } catch {
    // Storage refused: the tour still closes; the card will offer it again.
  }
  set(CLOSED);
}

// One step forward or back among `count` steps; past the last step finishes, before the first
// turns forward (a Back that skipped to the start has nowhere else to go).
export function moveTour(by: 1 | -1, count: number): void {
  if (state.step === null) return;
  const next = state.step + by;
  if (next >= count) {
    finishTour();
  } else if (next < 0) {
    set({ step: 0, direction: 1 });
  } else {
    set({ step: next, direction: by });
  }
}

// The open step's target is absent: keep moving the way the tour was going.
export function skipTour(count: number): void {
  if (state.step === null) return;
  moveTour(state.direction === -1 && state.step === 0 ? 1 : state.direction, count);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTour(): TourState {
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => CLOSED,
  );
}
