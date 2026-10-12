/**
 * Tests for the Devices view's live re-read: one read a second after the first security frame.
 *
 * These use real time (the delay is one second), so they are the slow ones and live apart from the
 * rest of the view's tests. The hook is exercised through the route component on the fake browser,
 * counting the requests it makes to `GET /v1/devices`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/devices/use_devices.ts and live/.
 *
 * Key invariants:
 *   - After the mount's reads, a read happens only because a security frame arrived.
 *   - A burst of frames costs one round of reads, and the answer replaces what was shown.
 *
 * See Also:
 *   - tests/views/devices/devices_view.test.tsx for the rest of the view.
 */
import { act, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DevicesScreen } from '../../../src/views/devices';
import { mountScreen } from './mount';
import { deviceView, securityFrame } from './support';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('the security re-read', () => {
  it('makes no read without a frame', async () => {
    const opened = await mountScreen(<DevicesScreen />);
    await screen.findByText('Pixel 9');
    // Longer than the one second window, so a read that was going to happen would have.
    await wait(1250);
    expect(opened.sent('GET /v1/devices')).toHaveLength(1);
    expect(opened.sent('GET /v1/entrance/mode')).toHaveLength(1);
    expect(opened.sent('GET /v1/entrance/confirmations')).toHaveLength(1);
  });

  it('reads once, a second after the first frame, however many follow', async () => {
    const opened = await mountScreen(<DevicesScreen />);
    await screen.findByText('Pixel 9');
    // The second read finds a device the frames did not carry.
    opened.fake.respond('GET /v1/devices', 200, {
      devices: [deviceView(), deviceView({ id: 'dev_late', name: 'Late tablet' })],
    });
    act(() => {
      opened.socket.receive(securityFrame('guard.entrance_redeemed'));
      opened.socket.receive(securityFrame('guard.entrance_approved'));
      opened.socket.receive(securityFrame('guard.entrance_locked'));
    });
    // Not before the window closes.
    await wait(500);
    expect(opened.sent('GET /v1/devices')).toHaveLength(1);
    expect(await screen.findByText('Late tablet', {}, { timeout: 2500 })).toBeTruthy();
    expect(opened.sent('GET /v1/devices')).toHaveLength(2);
    // The three frames cost one round: nothing else follows.
    await wait(1250);
    expect(opened.sent('GET /v1/devices')).toHaveLength(2);
    expect(opened.sent('GET /v1/entrance/mode')).toHaveLength(2);
    expect(opened.sent('GET /v1/entrance/confirmations')).toHaveLength(2);
  });

  it('opens one socket, on the Entrance security stream', async () => {
    const opened = await mountScreen(<DevicesScreen />);
    expect(opened.fake.sockets.map((s) => s.url)).toEqual(['wss://hive.test/v1/entrance/stream']);
  });
});
