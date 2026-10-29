/**
 * Tests for the guided tour: the steps in words, moving, skipping an absent target, the keyboard,
 * the route a step opens, which element a step accepts, and what the browser remembers.
 *
 * The panel is rendered with a table of its own over a few elements carrying `data-tour`, so the
 * tests do not depend on the shell; the shell test proves the real first step finds the strip.
 * jsdom has no layout, so every element on the page counts as shown, and an absent target is one
 * that is not on the page at all.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/help/tour/.
 *
 * Key invariants:
 *   - Each test starts the tour itself; it never opens on its own.
 *   - The tour's position is module state; unmounting the panel (the cleanup after each test)
 *     closes it, so every test starts closed.
 *
 * See Also:
 *   - tests/shell/shell.test.tsx for the tour inside the real shell.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { startTour, Tour } from '../../../src/views/help/tour';
import type { TourStep } from '../../../src/views/help/tour';

const STEPS: readonly TourStep[] = [
  { target: 'a', title: 'First', text: 'The first thing.' },
  { target: 'missing', title: 'Nowhere', text: 'Not on this page.' },
  { target: 'b', route: '#/forage', title: 'Last', text: 'The last thing.' },
];

function mount(steps: readonly TourStep[] = STEPS): () => void {
  const { unmount } = render(
    <>
      <div data-tour="a">a</div>
      <div data-tour="b">b</div>
      <div data-tour="view-title-fleet">Fleet</div>
      <Tour steps={steps} />
    </>,
  );
  act(() => startTour());
  return unmount;
}

beforeEach(() => {
  window.localStorage.clear();
  window.location.hash = '';
});

describe('Tour', () => {
  it('says the step in words, moves with Next and Back, and opens a step’s route', async () => {
    mount();
    const dialog = screen.getByRole('dialog', { name: 'First' });
    expect(screen.getByText('Step 1 of 3')).toBeTruthy();
    expect(document.activeElement).toBe(dialog);
    expect((screen.getByRole('button', { name: 'Back' }) as HTMLButtonElement).disabled).toBe(true);
    // The second step's target is absent, so Next lands on the third, which opens its route.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Step 3 of 3', {}, { timeout: 3000 })).toBeTruthy();
    expect(window.location.hash).toBe('#/forage');
    expect(screen.getByRole('button', { name: 'Finish' })).toBeTruthy();
    // Back skips the absent step the other way.
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByText('Step 1 of 3', {}, { timeout: 3000 })).toBeTruthy();
  });

  it('is not satisfied by another screen’s title row', async () => {
    // Only the Fleet's title row is on the page: a step about the Forage's is absent and skipped,
    // never outlined on the Fleet's, so the tour lands on the step about the Fleet.
    mount([
      { target: 'view-title-forage', title: 'Forage', text: 'Capacity.' },
      { target: 'view-title-fleet', title: 'Fleet', text: 'Every machine.' },
    ]);
    expect(await screen.findByRole('dialog', { name: 'Fleet' }, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByText('Step 2 of 2')).toBeTruthy();
  });

  it('remembers a finished tour, and not a skipped one', async () => {
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Finish' }, { timeout: 3000 }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(window.localStorage.getItem('hive.tour')).toBe('done');

    window.localStorage.clear();
    act(() => startTour());
    fireEvent.click(screen.getByRole('button', { name: 'Skip tour' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(window.localStorage.getItem('hive.tour')).toBeNull();
  });

  it('closes when the shell unmounts it, so the next session never opens mid-tour', async () => {
    const unmount = mount();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowRight' });
    expect(await screen.findByText('Step 3 of 3', {}, { timeout: 3000 })).toBeTruthy();
    unmount();
    render(<Tour steps={STEPS} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('answers the arrow keys and Escape on the panel', async () => {
    mount();
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'ArrowRight' });
    expect(await screen.findByText('Step 3 of 3', {}, { timeout: 3000 })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'ArrowLeft' });
    expect(await screen.findByText('Step 1 of 3', {}, { timeout: 3000 })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
