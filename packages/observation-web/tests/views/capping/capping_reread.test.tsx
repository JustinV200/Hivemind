/**
 * Tests for the Capping view's re-read: one read a second after the first `capping.*` frame, never without one.
 *
 * These use real time (the delay is one second), so they are the slow ones and live apart from the
 * rest of the view's tests. The hook is exercised through the route component on the fake browser,
 * counting the requests it makes to `GET /v1/capping`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/capping/use_capping.ts.
 *
 * Key invariants:
 *   - After the mount's read, a read happens only because a `capping.*` frame arrived.
 *   - A burst of frames costs one read, and the answer replaces what the frames folded.
 *
 * See Also:
 *   - tests/views/capping/capping_view.test.tsx for the rest of the view.
 */
import { act, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { auditRow, busyView, hop } from './support';
import { mount } from './harness';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('the trail re-read', () => {
  it('makes no read without a frame, and none for a frame of another family', async () => {
    const opened = await mount();
    await screen.findByRole('region', { name: 'Queue' });
    act(() => {
      opened.fake.sockets[0]!.receive(hop('alarm.raised', 'cell_1', { family: 'alarm' }));
    });
    // Longer than the one second window, so a read that was going to happen would have.
    await wait(1250);
    expect(opened.reads()).toBe(1);
  });

  it('reads once, a second after the first capping frame, however many follow', async () => {
    const opened = await mount();
    await screen.findByRole('region', { name: 'Queue' });
    // The answer to the second read has an audit the frames did not carry.
    opened.fake.respond('GET /v1/capping', 200, {
      ...busyView(),
      audits: [auditRow({ proposal_id: 'prop_late', bee_id: 'worker_7' })],
    });
    act(() => {
      const trail = opened.fake.sockets[0]!;
      trail.receive(hop('capping.checked', 'prop_wait'));
      trail.receive(hop('capping.capped', 'prop_wait', { at: '2026-01-01T00:01:01Z' }));
      trail.receive(hop('capping.applied', 'prop_wait', { at: '2026-01-01T00:01:02Z' }));
    });
    // Not before the window closes.
    await wait(500);
    expect(opened.reads()).toBe(1);
    const audits = within(await screen.findByRole('region', { name: 'Sampled audits' }));
    expect(await audits.findByLabelText('prop_late', {}, { timeout: 2500 })).toBeTruthy();
    expect(opened.reads()).toBe(2);
    // The three frames cost one read: nothing else follows.
    await wait(1250);
    expect(opened.reads()).toBe(2);
  });
});
