/**
 * Tests for the Enrol view: the invite, the waiting list, approve and deny.
 *
 * The view is mounted directly over a fake Entrance on a loopback session. An invite must come back
 * as a code, a link and a QR image, and be withdrawable; a device that redeemed it must appear in
 * the waiting list, live; approve and deny must post the bodies the document asks for; and a remote
 * session must see "Not found" and make no request at all.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/enrol/ end to end on the fake browser.
 *
 * Key invariants:
 *   - Assertions are on what the page shows and on the exact request bodies sent.
 *
 * See Also:
 *   - tests/views/devices/loopback_gate.test.tsx for the document-driven loopback gate.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EnrolScreen } from '../../../src/views/enrol';
import { mountScreen } from '../devices/mount';
import { bodyOf, deviceView, securityFrame } from '../devices/support';

const QR =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2"><path d="M0 0h1v1H0z"/></svg>';
const INVITE = {
  code: 'ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ',
  device_id: 'dev_invited',
  expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
  qr_svg: QR,
  url: 'https://hive.test/enrol#code=ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ',
};
const waiting = deviceView({
  id: 'dev_wait',
  name: 'phone',
  status: 'PENDING',
  approved_at: null,
  last_seen_at: null,
  fingerprint: 'AA:BB:CC:DD',
  description: { name: 'Pixel 9', platform: 'Android 16', user_agent: 'Chrome/150' },
  capabilities: ['observe', 'entrance:submit'],
  spend_cap_usd_per_day: 0,
});

const mount = (setup: Parameters<typeof mountScreen>[1] = {}) =>
  mountScreen(<EnrolScreen />, { listener: 'loopback', ...setup });

describe('the invite', () => {
  it('mints for a named device and shows the code, the link and a QR image with its expiry', async () => {
    const opened = await mount();
    opened.fake.respond('POST /v1/entrance/invites', 201, INVITE);
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'phone' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));

    const card = await screen.findByRole('region', { name: 'Invite' });
    expect(opened.sent('POST /v1/entrance/invites').map(bodyOf)).toEqual([{ label: 'phone' }]);
    expect(within(card).getByLabelText('Invite code').textContent).toBe(INVITE.code);
    expect(within(card).getByText(INVITE.url)).toBeTruthy();
    expect(card.querySelector('time')?.getAttribute('datetime')).toBe(INVITE.expires_at);
    const qr = within(card).getByAltText('QR code for the invite link') as HTMLImageElement;
    // The SVG rides in an image, so it cannot run anything; decoded it is the Entrance's own.
    expect(qr.src.startsWith('data:image/svg+xml')).toBe(true);
    expect(decodeURIComponent(qr.src.split(',').slice(1).join(','))).toBe(QR);
    // One invite at a time: the mint form is gone while this one is live.
    expect(screen.queryByRole('button', { name: 'Create invite' })).toBeNull();
  });

  it('draws no image for an answer that is not an SVG', async () => {
    const opened = await mount();
    opened.fake.respond('POST /v1/entrance/invites', 201, {
      ...INVITE,
      qr_svg: '<script>x</script>',
    });
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));
    await screen.findByRole('region', { name: 'Invite' });
    expect(screen.queryByAltText('QR code for the invite link')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
  });

  it('copies the link to the clipboard', async () => {
    const opened = await mount();
    const written: string[] = [];
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: (text: string) => (written.push(text), Promise.resolve()) },
    });
    opened.fake.respond('POST /v1/entrance/invites', 201, INVITE);
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Copy link' }));
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeTruthy();
    expect(written).toEqual([INVITE.url]);
  });

  it('says so when the invite has lapsed', async () => {
    const opened = await mount();
    opened.fake.respond('POST /v1/entrance/invites', 201, {
      ...INVITE,
      expires_at: '2020-01-01T00:00:00Z',
    });
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));
    expect(await screen.findByText(/This invite has expired/)).toBeTruthy();
  });

  it('withdraws the unredeemed invite and brings the mint form back', async () => {
    const opened = await mount();
    opened.fake.respond('POST /v1/entrance/invites', 201, INVITE);
    opened.fake.respond(
      'DELETE /v1/entrance/invites/dev_invited',
      200,
      deviceView({ id: 'dev_invited', status: 'EXPIRED' }),
    );
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Withdraw invite' }));
    expect(await screen.findByText('Invite withdrawn.')).toBeTruthy();
    expect(opened.sent('DELETE /v1/entrance/invites/dev_invited')).toHaveLength(1);
    expect(screen.queryByRole('region', { name: 'Invite' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Create invite' })).toBeTruthy();
  });

  it('drops the invite once the device it admits appears in the waiting list', async () => {
    const opened = await mount();
    opened.fake.respond('POST /v1/entrance/invites', 201, INVITE);
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));
    await screen.findByRole('region', { name: 'Invite' });
    opened.fake.respond('GET /v1/entrance/pending', 200, {
      devices: [{ ...waiting, id: 'dev_invited' }],
    });
    act(() => opened.socket.receive(securityFrame('guard.entrance_redeemed')));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Invite' })).toBeNull(), {
      timeout: 2500,
    });
    expect(screen.getByRole('listitem', { name: 'Waiting: phone' })).toBeTruthy();
  });

  it('shows a refusal of the mint', async () => {
    const opened = await mount();
    opened.fake.respond('POST /v1/entrance/invites', 422, {
      error: 'hivemind.entrance.invalid_request',
      detail: 'bad',
    });
    fireEvent.change(await screen.findByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create invite' }));
    expect(await screen.findByText('The Hive could not read that request.')).toBeTruthy();
  });
});

describe('the waiting list', () => {
  it('shows what the device said and the fingerprint to compare, escaped', async () => {
    await mount({
      pending: [
        { ...waiting, description: { name: 'Pixel 9', platform: '<b>x</b>', user_agent: 'UA' } },
        deviceView({ id: 'dev_invited_only', status: 'INVITED' }),
      ],
    });
    const card = await screen.findByRole('listitem', { name: 'Waiting: phone' });
    expect(within(card).getByText('AA:BB:CC:DD')).toBeTruthy();
    expect(within(card).getByText('<b>x</b>')).toBeTruthy();
    expect(card.querySelector('b')).toBeNull();
    // A device that has only been invited is not asking yet.
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
  });

  it('says what would fill it', async () => {
    await mount();
    expect(await screen.findByText(/Nobody is waiting/)).toBeTruthy();
  });

  it('approves with the name, capabilities, cap and expiry the operator bound', async () => {
    const opened = await mount({ pending: [waiting] });
    opened.fake.respond('POST /v1/entrance/pending/dev_wait/approve', 200, {
      ...waiting,
      status: 'APPROVED',
      name: 'Justin phone',
      bundle: null,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    const form = screen.getByRole('form', { name: 'Approve phone' });
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Justin phone' } });
    // The device's proposed set is ticked; the operator unticks one box and ticks another.
    fireEvent.click(within(form).getByLabelText(/\(entrance:submit\)$/));
    fireEvent.click(within(form).getByLabelText(/\(entrance:answer\)$/));
    fireEvent.change(within(form).getByLabelText('Spend cap per day in USD'), {
      target: { value: '12.5' },
    });
    fireEvent.change(within(form).getByLabelText('Expires'), { target: { value: '7' } });
    const before = Date.now();
    fireEvent.click(within(form).getByRole('button', { name: 'Approve device' }));

    expect(await screen.findByText('Approved Justin phone.')).toBeTruthy();
    const body = bodyOf(opened.sent('POST /v1/entrance/pending/dev_wait/approve')[0]!) as Record<
      string,
      unknown
    >;
    expect(body).toMatchObject({
      name: 'Justin phone',
      capabilities: ['observe', 'entrance:answer'],
      spend_cap_usd_per_day: 12.5,
      interactive: false,
    });
    // A week on from the moment of the click, to within the time the test took.
    const lapse = Date.parse(body.expires_at as string) - before;
    expect(Math.abs(lapse - 7 * 86_400_000)).toBeLessThan(5000);
    expect(screen.queryByRole('listitem', { name: 'Waiting: phone' })).toBeNull();
  });

  it("asks for the device role's own set when the capabilities are left blank, and for no expiry when none is chosen", async () => {
    const opened = await mount({ pending: [waiting] });
    opened.fake.respond('POST /v1/entrance/pending/dev_wait/approve', 200, {
      ...waiting,
      status: 'APPROVED',
      bundle: null,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    const form = screen.getByRole('form', { name: 'Approve phone' });
    fireEvent.click(within(form).getByLabelText(/\(observe\)$/));
    fireEvent.click(within(form).getByLabelText(/\(entrance:submit\)$/));
    fireEvent.change(within(form).getByLabelText('Expires'), { target: { value: 'never' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Approve device' }));
    await screen.findByText(/^Approved /);
    expect(bodyOf(opened.sent('POST /v1/entrance/pending/dev_wait/approve')[0]!)).toMatchObject({
      capabilities: null,
      expires_at: null,
    });
  });

  it('offers the interactive flag for a program key only, and sends it', async () => {
    const opened = await mount({ pending: [{ ...waiting, key_kind: 'ed25519' }] });
    opened.fake.respond('POST /v1/entrance/pending/dev_wait/approve', 200, {
      ...waiting,
      status: 'APPROVED',
      bundle: null,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    const form = screen.getByRole('form', { name: 'Approve phone' });
    fireEvent.click(within(form).getByLabelText('A person types the password at this device'));
    fireEvent.click(within(form).getByRole('button', { name: 'Approve device' }));
    await screen.findByText(/^Approved /);
    expect(bodyOf(opened.sent('POST /v1/entrance/pending/dev_wait/approve')[0]!)).toMatchObject({
      interactive: true,
    });
  });

  it('will not send without a spend cap', async () => {
    const opened = await mount({ pending: [waiting] });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    const form = screen.getByRole('form', { name: 'Approve phone' });
    fireEvent.change(within(form).getByLabelText('Spend cap per day in USD'), {
      target: { value: '' },
    });
    expect(within(form).getByRole('button', { name: 'Approve device' })).toHaveProperty(
      'disabled',
      true,
    );
    expect(opened.sent('POST /v1/entrance/pending/dev_wait/approve')).toHaveLength(0);
  });

  it('shows the sealed bundle once, with its passphrase, and drops it when dismissed', async () => {
    const opened = await mount({ pending: [waiting] });
    opened.fake.respond('POST /v1/entrance/pending/dev_wait/approve', 200, {
      ...waiting,
      status: 'APPROVED',
      bundle: { pkcs12_base64: 'QUJD', passphrase: 'correct-horse' },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve device' }));
    const notice = await screen.findByRole('status', { name: 'Certificate bundle' });
    expect(within(notice).getByText('correct-horse')).toBeTruthy();
    expect(within(notice).getByRole('link', { name: 'Save the file' }).getAttribute('href')).toBe(
      'data:application/x-pkcs12;base64,QUJD',
    );
    fireEvent.click(within(notice).getByRole('button', { name: 'I have it' }));
    expect(screen.queryByText('correct-horse')).toBeNull();
  });

  it("denies with the Entrance's own reason", async () => {
    const opened = await mount({ pending: [waiting] });
    opened.fake.respond('POST /v1/entrance/pending/dev_wait/deny', 200, {
      ...waiting,
      status: 'DENIED',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Deny phone' }));
    // The first tap only arms it; nothing is sent until the second.
    expect(opened.sent('POST /v1/entrance/pending/dev_wait/deny')).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Deny now' }));
    expect(await screen.findByText('Denied phone.')).toBeTruthy();
    expect(opened.sent('POST /v1/entrance/pending/dev_wait/deny').map(bodyOf)).toEqual([
      { reason: 'denied by the operator' },
    ]);
    expect(screen.queryByRole('listitem', { name: 'Waiting: phone' })).toBeNull();
  });

  it('shows a refusal of the approval in place and keeps the device waiting', async () => {
    const opened = await mount({ pending: [waiting] });
    opened.fake.respond('POST /v1/entrance/pending/dev_wait/approve', 403, {
      error: 'hivemind.entrance.capability_beyond_ceiling',
      detail: 'too wide',
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve device' }));
    expect(await screen.findByText('A device may not be given that much.')).toBeTruthy();
    expect(screen.getByRole('listitem', { name: 'Waiting: phone' })).toBeTruthy();
  });

  it('retries an approval once after a step-up', async () => {
    const opened = await mount({ pending: [waiting] });
    let refused = false;
    opened.fake.routes.set('POST /v1/entrance/pending/dev_wait/approve', () =>
      refused
        ? { status: 200, body: { ...waiting, status: 'APPROVED', bundle: null } }
        : ((refused = true),
          { status: 403, body: { error: 'hivemind.entrance.step_up_required', detail: 'x' } }),
    );
    opened.fake.respond('POST /v1/auth/step-up/challenge', 200, {
      nonce: 'n',
      passkey_options: {},
    });
    opened.fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:00:00Z' });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve phone' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve device' }));
    const prompt = await screen.findByRole('form', { name: 'Confirm again' });
    fireEvent.change(within(prompt).getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(within(prompt).getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText(/^Approved /)).toBeTruthy();
    expect(opened.sent('POST /v1/entrance/pending/dev_wait/approve')).toHaveLength(2);
  });
});

describe('off the Hive Stand', () => {
  it('shows "Not found" and makes no request and opens no socket', async () => {
    const { openHive } = await import('../devices/support');
    const { render } = await import('@testing-library/react');
    const { HiveProvider } = await import('../../../src/store');
    const opened = await openHive({ listener: 'remote' });
    render(
      <HiveProvider hive={opened.hive}>
        <EnrolScreen />
      </HiveProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'Not found' })).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(opened.fake.requests).toHaveLength(0);
    expect(opened.fake.sockets).toHaveLength(0);
  });
});
