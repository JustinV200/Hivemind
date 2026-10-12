/**
 * Tests for the Devices slice: replacing reads, folding the writes' answers, counting frames.
 *
 * The reducer is pure, so these run on plain objects. They pin what the view relies on: a read
 * replaces its list whole, a failure belongs to its own read, a frame only counts, and a write's
 * answer changes one row or the mode at once.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/devices/slice.ts.
 *
 * Key invariants:
 *   - A reducer that changes nothing returns the state it was given.
 *
 * See Also:
 *   - tests/views/devices/devices_view.test.tsx for the slice inside the view.
 */
import { describe, expect, it } from 'vitest';
import { initialDevices, MAX_DEVICES, reduceDevices } from '../../../src/views/devices/slice';
import { deviceView, heldView, modeView, securityFrame } from './support';

describe('reads', () => {
  it('replace the list whole and mark the slice seeded', () => {
    const first = reduceDevices(initialDevices, { type: 'devices', devices: [deviceView()] });
    expect(first.seeded).toBe(true);
    const second = reduceDevices(first, {
      type: 'devices',
      devices: [deviceView({ id: 'dev_b', name: 'B' })],
    });
    expect(second.devices.map((d) => d.id)).toEqual(['dev_b']);
  });

  it('keep a failure under the read that failed, and clear it when that read works', () => {
    const failed = reduceDevices(initialDevices, {
      type: 'failed',
      read: 'confirmations',
      error: 'x',
    });
    expect(failed.errors).toEqual({ devices: null, mode: null, confirmations: 'x' });
    const listed = reduceDevices(failed, { type: 'devices', devices: [] });
    expect(listed.errors.confirmations).toBe('x');
    const healed = reduceDevices(listed, { type: 'held', held: [] });
    expect(healed.errors.confirmations).toBeNull();
  });

  it('are bounded', () => {
    const many = Array.from({ length: MAX_DEVICES + 5 }, (_, i) => deviceView({ id: `d${i}` }));
    expect(reduceDevices(initialDevices, { type: 'devices', devices: many }).devices).toHaveLength(
      MAX_DEVICES,
    );
  });
});

describe('frames and writes', () => {
  it('count a frame and nothing else, and forget the count on open', () => {
    const counted = reduceDevices(initialDevices, { type: 'security', frame: securityFrame() });
    expect(counted).toEqual({ ...initialDevices, ticks: 1 });
    expect(reduceDevices(counted, { type: 'open' }).ticks).toBe(0);
    expect(reduceDevices(initialDevices, { type: 'open' })).toBe(initialDevices);
  });

  it('replace one row with the answer of a write', () => {
    const state = reduceDevices(initialDevices, {
      type: 'devices',
      devices: [deviceView(), deviceView({ id: 'dev_b', name: 'B' })],
    });
    const next = reduceDevices(state, {
      type: 'device_changed',
      device: deviceView({ status: 'LOCKED' }),
    });
    expect(next.devices.map((d) => d.status)).toEqual(['LOCKED', 'APPROVED']);
  });

  it('move the mode, and drop a held request that was dealt with', () => {
    let state = reduceDevices(initialDevices, { type: 'mode', mode: modeView() });
    state = reduceDevices(state, { type: 'mode_changed', mode: 'REDUCED' });
    expect(state.mode?.mode).toBe('REDUCED');
    state = reduceDevices(state, { type: 'held', held: [heldView(), heldView({ id: 'pend_2' })] });
    state = reduceDevices(state, { type: 'held_done', id: 'pend_1' });
    expect(state.held?.map((h) => h.id)).toEqual(['pend_2']);
    // Before the mode is read there is nothing to move.
    expect(reduceDevices(initialDevices, { type: 'mode_changed', mode: 'OPEN' })).toBe(
      initialDevices,
    );
  });
});
