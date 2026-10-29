/**
 * Tests for the Devices view: the list, the loopback-only controls, revoke, step-up and the Entrance.
 *
 * The view is mounted directly over a fake Entrance. The list must show each device's standing, what
 * it may do, its cap and when it was last seen, and mark the device in hand; the controls that call
 * loopback-only routes must be absent on a remote session and present on the Hive Stand; revoke must
 * not send until the device's name is typed; and a write refused for want of a step-up must ask,
 * step up and retry exactly once.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/devices/ end to end on the fake browser.
 *
 * Key invariants:
 *   - Assertions are on what the page shows and on what was sent, not on internals.
 *   - The live re-read has its own file, as it waits in real time.
 *
 * See Also:
 *   - tests/views/devices/loopback_gate.test.tsx for the document-driven gate.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DevicesScreen } from '../../../src/views/devices';
import { mountScreen } from './mount';
import type { Mounted } from './mount';
import { bodyOf, deviceView, heldView, modeView } from './support';

const STEP_UP = {
  error: 'hivemind.entrance.step_up_required',
  detail: 'Step up first.',
  reason: 'sensitive_action',
};

const here = deviceView({ id: 'dev_test', name: 'Hive Stand console', loopback_bound: true });
const phone = deviceView();
const locked = deviceView({ id: 'dev_old', name: 'Old tablet', status: 'LOCKED' });

const mount = (setup: Parameters<typeof mountScreen>[1]) => mountScreen(<DevicesScreen />, setup);

describe('the list', () => {
  it('shows each device with its standing, capabilities, cap, last seen and platform', async () => {
    await mount({ devices: [phone, locked] });
    const row = (await screen.findByText('Pixel 9')).closest('tr')!;
    const cells = within(row);
    // The standing is a word with the wire code on its title, like every other code on screen.
    expect(cells.getByText('approved').getAttribute('title')).toBe('APPROVED');
    expect(
      cells.getByText(
        'may watch the Hive (observe); may send goals and messages (entrance:submit)',
      ),
    ).toBeTruthy();
    expect(cells.getByText('$5.00 per day')).toBeTruthy();
    expect(cells.getByText('Android 16')).toBeTruthy();
    expect(cells.getByText('does not expire')).toBeTruthy();
    // The last connection is a time element carrying the exact ISO moment.
    expect(row.querySelector('time')?.getAttribute('datetime')).toBe('2026-01-02T03:04:05Z');
    expect(screen.getByText('locked')).toBeTruthy();
  });

  it('says "never" and "uncapped" for what the Entrance leaves null, and marks the device in hand', async () => {
    await mount({
      devices: [
        here,
        deviceView({ id: 'dev_new', name: 'New', last_seen_at: null, spend_cap_usd_per_day: null }),
      ],
    });
    const mine = (await screen.findByText('Hive Stand console')).closest('tr')!;
    expect(within(mine).getByText('this device')).toBeTruthy();
    expect(within(mine).getByText('Hive Stand')).toBeTruthy();
    const other = screen.getByText('New').closest('tr')!;
    expect(within(other).getByText('never')).toBeTruthy();
    expect(within(other).getByText('uncapped')).toBeTruthy();
  });

  it("draws a device's own words as text, never as markup", async () => {
    const hostile = deviceView({
      id: 'dev_x',
      name: '<img src=x onerror=alert(1)>',
      description: { name: 'x', platform: '<b>bold</b>', user_agent: '' },
    });
    await mount({ devices: [hostile] });
    await screen.findByText('<img src=x onerror=alert(1)>');
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('td b')).toBeNull();
  });

  it('says what would fill an empty list, and shows a refusal rather than a blank screen', async () => {
    const empty = await mount({ devices: [] });
    expect(await screen.findByText(/No devices yet/)).toBeTruthy();
    expect(empty.paths()).toEqual(
      expect.arrayContaining([
        'GET /v1/devices',
        'GET /v1/entrance/mode',
        'GET /v1/entrance/confirmations',
      ]),
    );
  });

  it('shows the list when only the held requests are refused', async () => {
    const opened = await mount({ devices: [phone] });
    await screen.findByText('Pixel 9');
    opened.fake.respond('GET /v1/entrance/confirmations', 403, {
      error: 'hivemind.entrance.capability_denied',
      detail: 'no',
      capability: 'entrance:submit',
    });
    opened.socket.receive({
      type: 'security_event',
      event: {
        id: 'e',
        kind: 'k',
        subject_id: 's',
        actor: 'a',
        at: '2026-01-01T00:00:00Z',
        payload: {},
      },
    });
    expect(
      await screen.findByText(
        'This device is not allowed to see or do that.',
        {},
        { timeout: 2500 },
      ),
    ).toBeTruthy();
    expect(screen.getByText('Pixel 9')).toBeTruthy();
  });
});

describe('the controls', () => {
  it('offers none on a remote session', async () => {
    await mount({ listener: 'remote', devices: [phone, locked] });
    await screen.findByText('Pixel 9');
    for (const name of [/^Lock /, /^Unlock /, /^Revoke /, /^Edit capabilities/, 'Controls']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    expect(screen.queryByRole('columnheader', { name: 'Controls' })).toBeNull();
  });

  it('offers lock, edit and revoke on an approved device and unlock and revoke on a locked one, on the Hive Stand', async () => {
    await mount({ listener: 'loopback', devices: [here, phone, locked] });
    await screen.findByText('Pixel 9');
    expect(screen.getByRole('button', { name: 'Lock Pixel 9' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit capabilities of Pixel 9' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Revoke Pixel 9' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Unlock Old tablet' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Revoke Old tablet' })).toBeTruthy();
    // The device in hand offers nothing: no one locks themselves out by a stray tap.
    expect(screen.queryByRole('button', { name: /Hive Stand console/ })).toBeNull();
  });

  it('locks and unlocks, posting to the device and showing the new standing', async () => {
    const opened = await mount({ listener: 'loopback', devices: [phone, locked] });
    opened.fake.respond('POST /v1/devices/dev_phone/lock', 200, { ...phone, status: 'LOCKED' });
    opened.fake.respond('POST /v1/devices/dev_old/unlock', 200, { ...locked, status: 'APPROVED' });
    fireEvent.click(await screen.findByRole('button', { name: 'Lock Pixel 9' }));
    expect(await screen.findByText('Locked Pixel 9.')).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Unlock Pixel 9' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Unlock Old tablet' }));
    expect(await screen.findByText('Unlocked Old tablet.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Lock Old tablet' })).toBeTruthy();
  });

  it('replaces capabilities and the cap with the whole new set', async () => {
    const opened = await mount({ listener: 'loopback', devices: [phone] });
    opened.fake.respond('POST /v1/devices/dev_phone/capabilities', 200, {
      ...phone,
      capabilities: ['observe'],
      spend_cap_usd_per_day: 1.5,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit capabilities of Pixel 9' }));
    const form = screen.getByRole('form', { name: 'Capabilities of Pixel 9' });
    // The device's set is ticked in the known list, each box a phrase with its code.
    expect(within(form).getByLabelText(/\(observe\)$/)).toHaveProperty('checked', true);
    fireEvent.click(within(form).getByLabelText(/\(entrance:submit\)$/));
    fireEvent.change(within(form).getByLabelText(/Spend cap per day/), {
      target: { value: '1.5' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Save capabilities' }));
    await screen.findByText('Saved what Pixel 9 may do.');
    expect(opened.sent('POST /v1/devices/dev_phone/capabilities').map(bodyOf)).toEqual([
      { capabilities: ['observe'], spend_cap_usd_per_day: 1.5 },
    ]);
    expect(screen.getByText('$1.50 per day')).toBeTruthy();
  });

  it('will not send an empty capability set', async () => {
    const opened = await mount({ listener: 'loopback', devices: [phone] });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit capabilities of Pixel 9' }));
    const form = screen.getByRole('form', { name: 'Capabilities of Pixel 9' });
    fireEvent.click(within(form).getByLabelText(/\(observe\)$/));
    fireEvent.click(within(form).getByLabelText(/\(entrance:submit\)$/));
    expect(within(form).getByRole('button', { name: 'Save capabilities' })).toHaveProperty(
      'disabled',
      true,
    );
    expect(opened.sent('POST /v1/devices/dev_phone/capabilities')).toHaveLength(0);
  });

  // A capability the known list does not hold (a pinned Cell's) is still the device's: a save
  // that dropped it would silently narrow the device.
  it('keeps a capability the known list does not hold, ticked and sent back', async () => {
    const pinned = deviceView({ capabilities: ['observe', 'cell:real:cell_x'] });
    const opened = await mount({ listener: 'loopback', devices: [pinned] });
    opened.fake.respond('POST /v1/devices/dev_phone/capabilities', 200, pinned);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit capabilities of Pixel 9' }));
    const form = screen.getByRole('form', { name: 'Capabilities of Pixel 9' });
    expect(within(form).getByLabelText(/\(observe\)$/)).toHaveProperty('checked', true);
    expect(within(form).getByLabelText('cell:real:cell_x')).toHaveProperty('checked', true);
    fireEvent.click(within(form).getByRole('button', { name: 'Save capabilities' }));
    await screen.findByText('Saved what Pixel 9 may do.');
    expect(opened.sent('POST /v1/devices/dev_phone/capabilities').map(bodyOf)).toEqual([
      { capabilities: ['observe', 'cell:real:cell_x'], spend_cap_usd_per_day: 5 },
    ]);
  });
});

describe('revoke', () => {
  async function openRevoke(): Promise<Mounted> {
    const opened = await mount({ listener: 'loopback', devices: [phone] });
    opened.fake.respond('POST /v1/devices/dev_phone/revoke', 200, {
      device: { ...phone, status: 'REVOKED' },
      goals_cancelled: ['goal_1', 'goal_2'],
      goals_left_running: ['goal_3'],
      requests_refused: [],
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke Pixel 9' }));
    return opened;
  }

  it('waits for the exact name, then sends and says what it did', async () => {
    const opened = await openRevoke();
    const form = screen.getByRole('form', { name: 'Revoke device Pixel 9' });
    const send = within(form).getByRole('button', { name: 'Revoke device' });
    expect(send).toHaveProperty('disabled', true);
    const field = within(form).getByLabelText('Type Pixel 9 to confirm');
    fireEvent.change(field, { target: { value: 'pixel 9' } });
    expect(send).toHaveProperty('disabled', true);
    fireEvent.change(field, { target: { value: 'Pixel 9' } });
    expect(send).toHaveProperty('disabled', false);
    fireEvent.click(within(form).getByLabelText('Also cancel its open goals'));
    fireEvent.click(send);
    expect(
      await screen.findByText(
        'Revoked Pixel 9. Cancelled 2 open goals. 1 goal still running: goal_3.',
      ),
    ).toBeTruthy();
    expect(opened.sent('POST /v1/devices/dev_phone/revoke').map(bodyOf)).toEqual([
      { cancel_goals: true },
    ]);
    expect(screen.getByText('revoked')).toBeTruthy();
    expect(screen.queryByRole('form', { name: 'Revoke device Pixel 9' })).toBeNull();
  });

  it('leaves the goals alone unless asked, and sends nothing on cancel', async () => {
    const opened = await openRevoke();
    const form = screen.getByRole('form', { name: 'Revoke device Pixel 9' });
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }));
    expect(opened.sent('POST /v1/devices/dev_phone/revoke')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Revoke Pixel 9' }));
    const again = screen.getByRole('form', { name: 'Revoke device Pixel 9' });
    fireEvent.change(within(again).getByLabelText('Type Pixel 9 to confirm'), {
      target: { value: 'Pixel 9' },
    });
    fireEvent.click(within(again).getByRole('button', { name: 'Revoke device' }));
    await waitFor(() => expect(opened.sent('POST /v1/devices/dev_phone/revoke')).toHaveLength(1));
    expect(bodyOf(opened.sent('POST /v1/devices/dev_phone/revoke')[0]!)).toEqual({
      cancel_goals: false,
    });
  });

  it('shows the refusal when the Hive Stand console is protected', async () => {
    const opened = await openRevoke();
    opened.fake.respond('POST /v1/devices/dev_phone/revoke', 403, {
      error: 'hivemind.entrance.console_protected',
      detail: 'The console stays.',
    });
    const form = screen.getByRole('form', { name: 'Revoke device Pixel 9' });
    fireEvent.change(within(form).getByLabelText('Type Pixel 9 to confirm'), {
      target: { value: 'Pixel 9' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Revoke device' }));
    expect(await screen.findByText('The Hive Stand console cannot be revoked.')).toBeTruthy();
    expect(
      document.querySelector('[data-code="hivemind.entrance.console_protected"]'),
    ).toBeTruthy();
  });
});

describe('step-up', () => {
  function needStepUp(opened: Mounted, key: string, ok: unknown, refusals = 1): void {
    let refused = 0;
    opened.fake.routes.set(key, () =>
      refused < refusals ? (refused++, { status: 403, body: STEP_UP }) : { status: 200, body: ok },
    );
    opened.fake.respond('POST /v1/auth/step-up/challenge', 200, {
      nonce: 'n1',
      passkey_options: {},
    });
    opened.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
  }

  it('asks for the password, steps up through the passkey, and retries the write once', async () => {
    const opened = await mount({ listener: 'loopback', devices: [phone] });
    needStepUp(opened, 'POST /v1/devices/dev_phone/capabilities', {
      ...phone,
      capabilities: ['observe'],
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit capabilities of Pixel 9' }));
    const form = screen.getByRole('form', { name: 'Capabilities of Pixel 9' });
    fireEvent.click(within(form).getByLabelText(/\(entrance:submit\)$/));
    fireEvent.click(within(form).getByRole('button', { name: 'Save capabilities' }));

    const prompt = await screen.findByRole('form', { name: 'Confirm again' });
    expect(prompt.textContent).toContain('This needs you to confirm again.');
    fireEvent.change(within(prompt).getByLabelText('Password'), { target: { value: 'hunter2' } });
    fireEvent.click(within(prompt).getByRole('button', { name: 'Confirm' }));

    await screen.findByText('Saved what Pixel 9 may do.');
    expect(opened.sent('POST /v1/devices/dev_phone/capabilities')).toHaveLength(2);
    expect(opened.sent('POST /v1/devices/dev_phone/capabilities').map(bodyOf)[0]).toEqual(
      opened.sent('POST /v1/devices/dev_phone/capabilities').map(bodyOf)[1],
    );
    expect(opened.fake.passkeyOptions).toHaveLength(1);
    expect(bodyOf(opened.sent('POST /v1/auth/step-up')[0]!)).toMatchObject({ password: 'hunter2' });
    expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull();
  });

  it('does not ask a second time when the retry is refused again', async () => {
    const opened = await mount({ listener: 'loopback', devices: [phone] });
    needStepUp(opened, 'POST /v1/devices/dev_phone/lock', phone, 2);
    fireEvent.click(await screen.findByRole('button', { name: 'Lock Pixel 9' }));
    const prompt = await screen.findByRole('form', { name: 'Confirm again' });
    fireEvent.change(within(prompt).getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(within(prompt).getByRole('button', { name: 'Confirm' }));
    await waitFor(() =>
      expect(
        document.querySelector('[data-code="hivemind.entrance.step_up_required"]'),
      ).toBeTruthy(),
    );
    expect(opened.sent('POST /v1/devices/dev_phone/lock')).toHaveLength(2);
    expect(opened.sent('POST /v1/auth/step-up')).toHaveLength(1);
    expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull();
  });

  it('keeps asking after a wrong password, and gives the refusal back on cancel', async () => {
    const opened = await mount({ listener: 'loopback', devices: [phone] });
    needStepUp(opened, 'POST /v1/devices/dev_phone/lock', phone);
    opened.fake.respond('POST /v1/auth/step-up', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'no',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Lock Pixel 9' }));
    fireEvent.change(await screen.findByLabelText('Password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('The Hive did not accept this login.')).toBeTruthy();
    // The prompt stays, with a clean field for another try.
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull());
    expect(opened.sent('POST /v1/devices/dev_phone/lock')).toHaveLength(1);
    expect(await screen.findByText('This needs you to confirm again.')).toBeTruthy();
  });

  it('shows a standing panel while the session needs a step-up, and clears the flag when it is done', async () => {
    const opened = await mount({ needsStepUp: true, devices: [phone] });
    opened.fake.respond('POST /v1/auth/step-up/challenge', 200, {
      nonce: 'n',
      passkey_options: {},
    });
    opened.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
    const panel = await screen.findByRole('form', { name: 'Confirm again' });
    fireEvent.change(within(panel).getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull());
    expect(opened.hive.store.getSnapshot().session.needsStepUp).toBe(false);
  });

  it('shows no panel when the session needs no step-up', async () => {
    await mount({ devices: [phone] });
    await screen.findByText('Pixel 9');
    expect(screen.queryByRole('form', { name: 'Confirm again' })).toBeNull();
  });
});

describe('the Entrance panel', () => {
  it('shows the mode and offers to reduce from any listener, after a second tap', async () => {
    const opened = await mount({ listener: 'remote', mode: modeView() });
    opened.fake.respond('POST /v1/entrance/reduce', 200, { changed: true, mode: 'REDUCED' });
    await screen.findByText('on (serving)');
    fireEvent.click(screen.getByRole('button', { name: 'Reduce to the Hive Stand only' }));
    expect(opened.sent('POST /v1/entrance/reduce')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Reduce now' }));
    expect(
      await screen.findByText('The Entrance now answers on the Hive Stand only.'),
    ).toBeTruthy();
    expect(screen.getByText('reduced, the Hive Stand only')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Reopen the Entrance' })).toBeNull();
    expect(screen.getByText('Only the Hive Stand can reopen the Entrance.')).toBeTruthy();
  });

  it('reopens only on the Hive Stand, behind a step-up', async () => {
    const opened = await mount({
      listener: 'loopback',
      mode: modeView({ mode: 'REDUCED', remote_listening: false }),
    });
    let refused = false;
    opened.fake.routes.set('POST /v1/entrance/open', () =>
      refused
        ? { status: 200, body: { changed: true, mode: 'OPEN' } }
        : ((refused = true), { status: 403, body: STEP_UP }),
    );
    opened.fake.respond('POST /v1/auth/step-up/challenge', 200, {
      nonce: 'n',
      passkey_options: {},
    });
    opened.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
    fireEvent.click(await screen.findByRole('button', { name: 'Reopen the Entrance' }));
    const prompt = await screen.findByRole('form', { name: 'Confirm again' });
    fireEvent.change(within(prompt).getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(within(prompt).getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('The Entrance is open again.')).toBeTruthy();
    expect(opened.sent('POST /v1/entrance/open')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Reduce to the Hive Stand only' })).toBeTruthy();
  });

  it('lists held requests with their payload, and confirms or declines them', async () => {
    const opened = await mount({
      devices: [deviceView({ id: 'dev_bot', name: 'Garden bot' })],
      held: [heldView(), heldView({ id: 'pend_2', action: 'new_network' })],
    });
    opened.fake.respond('POST /v1/entrance/confirmations/pend_1/confirm', 200, {
      pending_id: 'pend_1',
      action: 'goal',
      goal_request_id: 'goalreq_9',
    });
    opened.fake.respond(
      'POST /v1/entrance/confirmations/pend_2/cancel',
      200,
      heldView({ id: 'pend_2', status: 'CANCELLED' }),
    );
    const first = await screen.findByRole('listitem', { name: 'Held request pend_1' });
    expect(within(first).getByText(/from Garden bot/)).toBeTruthy();
    expect(first.textContent).toContain('rebuild the garden');
    fireEvent.click(within(first).getByRole('button', { name: 'Confirm held request pend_1' }));
    expect(
      await screen.findByText('Confirmed. The goal request goalreq_9 is committed.'),
    ).toBeTruthy();
    expect(screen.queryByRole('listitem', { name: 'Held request pend_1' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Decline held request pend_2' }));
    expect(await screen.findByText('Declined.')).toBeTruthy();
    expect(await screen.findByText(/Nothing is held/)).toBeTruthy();
  });
});
