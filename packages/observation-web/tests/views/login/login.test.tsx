/**
 * Tests for the login screen and the join screen it shows: the invite, the passkey, the wait.
 *
 * The whole app is mounted on the fake browser with no stored session, so the first screen is the
 * login screen. With an invite in the address and no device on the browser it must show the join
 * screen; the join flow must make the passkey through the seam, redeem, wait without polling, answer
 * "check again" only when tapped, and go on to login with the new device filled in. A browser that
 * already has a device must see login first, with a way to join instead.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/login/ and src/views/enrol/join/ through
 *   src/app.tsx.
 *
 * Key invariants:
 *   - No request is made by the join screen until a button is tapped, and none repeats by itself.
 *
 * See Also:
 *   - tests/landing_board/client/session.test.ts for the client calls underneath.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import App from '../../../src/app';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { bodyOf } from '../devices/support';

const CODE = 'ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ';
const CREATION = { challenge: 'Y3JlYXRl', rp: { id: 'hive.test' }, user: { id: 'dXNlcg' } };
const NO_LOGIN = { error: 'hivemind.entrance.authentication_failed', detail: 'no' };

afterEach(() => {
  window.history.replaceState(null, '', '/');
});

function scripted(hash: string, remembered?: string): FakeBrowser {
  const fake = createFakeBrowser();
  window.history.replaceState(null, '', `/enrol${hash}`);
  fake.respond('POST /v1/enrol/passkey-options', 200, { options: CREATION });
  fake.respond('POST /v1/enrol/passkey', 202, {
    device_id: 'dev_new',
    fingerprint: 'AB:CD:EF:01',
    hive_public_key_hex: 'ff',
  });
  fake.respond('POST /v1/auth/challenge', 401, NO_LOGIN);
  if (remembered) void fake.store.put('device', remembered);
  return fake;
}

const posts = (fake: FakeBrowser, target: string) =>
  fake.requests.filter((r) => r.method === 'POST' && r.target === target);

async function nameAndJoin(name = 'phone'): Promise<void> {
  fireEvent.change(await screen.findByLabelText('Name this device'), { target: { value: name } });
  fireEvent.click(screen.getByRole('button', { name: 'Make passkey and join' }));
}

describe('the join screen', () => {
  it('replaces the login form when the address carries an invite and the browser has no device', async () => {
    const fake = scripted(`#code=${CODE}`);
    render(<App browser={fake.browser} />);
    expect(await screen.findByRole('heading', { name: 'Join the Observation Hive' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Log in to the Observation Hive' })).toBeNull();
    // Nothing is sent until the human taps: not even a look at the invite.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(fake.requests).toHaveLength(0);
    // The code itself is never drawn.
    expect(document.body.textContent).not.toContain(CODE);
  });

  it('makes the passkey through the seam, redeems the invite and shows the fingerprint to read out', async () => {
    const fake = scripted(`#code=${CODE}`);
    render(<App browser={fake.browser} />);
    await nameAndJoin('  Justin phone ');
    expect(await screen.findByRole('heading', { name: 'Waiting for the operator' })).toBeTruthy();
    expect(screen.getByLabelText('Key fingerprint').textContent).toBe('AB:CD:EF:01');

    expect(fake.creationOptions).toEqual([CREATION]);
    expect(posts(fake, '/v1/enrol/passkey-options').map(bodyOf)).toEqual([{ code: CODE }]);
    const redeemed = bodyOf(posts(fake, '/v1/enrol/passkey')[0]!) as {
      code: string;
      registration: unknown;
      description: { name: string; user_agent: unknown };
    };
    expect(redeemed.code).toBe(CODE);
    expect(redeemed.registration).toEqual({
      id: 'new-credential',
      type: 'public-key',
      response: {},
    });
    expect(redeemed.description).toMatchObject({ name: 'Justin phone' });
    expect(typeof redeemed.description.user_agent).toBe('string');
    // The spent code leaves the address, and the device id is kept for the login that follows.
    expect(window.location.hash).toBe('');
    expect(await fake.store.get('device')).toBe('dev_new');
  });

  it('waits without polling, and asks only when the human taps Check again', async () => {
    const fake = scripted(`#code=${CODE}`);
    render(<App browser={fake.browser} />);
    await nameAndJoin();
    await screen.findByRole('heading', { name: 'Waiting for the operator' });
    await new Promise((resolve) => setTimeout(resolve, 100));
    // No timer was set for a check: the fake clock holds none, and no challenge has been asked.
    expect(fake.pendingTimers()).toBe(0);
    expect(posts(fake, '/v1/auth/challenge')).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('Not approved yet.')).toBeTruthy();
    expect(posts(fake, '/v1/auth/challenge')).toHaveLength(1);
    expect(bodyOf(posts(fake, '/v1/auth/challenge')[0]!)).toMatchObject({ device_id: 'dev_new' });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(posts(fake, '/v1/auth/challenge')).toHaveLength(1);
  });

  it('moves on to login with the new device filled in once the operator has approved', async () => {
    const fake = scripted(`#code=${CODE}`);
    render(<App browser={fake.browser} />);
    await nameAndJoin();
    await screen.findByRole('heading', { name: 'Waiting for the operator' });
    fake.respond('POST /v1/auth/challenge', 200, {
      device_id: 'dev_new',
      nonce: 'n',
      expires_at: '2030-01-01T00:00:00Z',
      passkey_options: { challenge: 'abc' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByRole('heading', { name: 'Approved' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Go to login' }));

    const device = (await screen.findByLabelText('Device')) as HTMLInputElement;
    await waitFor(() => expect(device.value).toBe('dev_new'));
    expect(screen.getByRole('heading', { name: 'Log in to the Observation Hive' })).toBeTruthy();
    // The invite is spent, so there is no way back to joining with it.
    expect(screen.queryByRole('button', { name: /Join with the invite/ })).toBeNull();
  });

  it('keeps the human on the form with the refusal when the invite is not accepted', async () => {
    const fake = scripted(`#code=${CODE}`);
    fake.respond('POST /v1/enrol/passkey-options', 403, {
      error: 'hivemind.entrance.enrolment_refused',
      detail: 'x',
    });
    render(<App browser={fake.browser} />);
    await nameAndJoin();
    expect(await screen.findByText('The invite was not accepted.')).toBeTruthy();
    expect(screen.getByText('Ask the operator for a new invite.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Make passkey and join' })).toBeTruthy();
    expect(fake.creationOptions).toEqual([]);
    expect(await fake.store.get('device')).toBeUndefined();
  });

  it('says a cancelled passkey prompt is that, and lets the human try again', async () => {
    const fake = scripted(`#code=${CODE}`);
    fake.browser.createPasskey = () => Promise.reject(new DOMException('no', 'NotAllowedError'));
    render(<App browser={fake.browser} />);
    await nameAndJoin();
    expect(await screen.findByText(/passkey prompt was cancelled/)).toBeTruthy();
    expect(posts(fake, '/v1/enrol/passkey')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Make passkey and join' })).toHaveProperty(
      'disabled',
      false,
    );
  });

  it('does not offer the join button without a name', async () => {
    render(<App browser={scripted(`#code=${CODE}`).browser} />);
    const button = await screen.findByRole('button', { name: 'Make passkey and join' });
    expect(button).toHaveProperty('disabled', true);
  });
});

describe('a browser that already has a device', () => {
  it('shows login first, with a way to join with the invite, and a way back', async () => {
    const fake = scripted(`#code=${CODE}`, 'dev_old');
    render(<App browser={fake.browser} />);
    expect(
      await screen.findByRole('heading', { name: 'Log in to the Observation Hive' }),
    ).toBeTruthy();
    expect((screen.getByLabelText('Device') as HTMLInputElement).value).toBe('dev_old');
    fireEvent.click(screen.getByRole('button', { name: 'Join with the invite in this link' }));
    expect(await screen.findByRole('heading', { name: 'Join the Observation Hive' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Log in with this device instead' }));
    expect(
      await screen.findByRole('heading', { name: 'Log in to the Observation Hive' }),
    ).toBeTruthy();
  });
});

describe('the login screen', () => {
  it('shows only the login form when the address carries no invite', async () => {
    const fake = scripted('');
    render(<App browser={fake.browser} />);
    expect(
      await screen.findByRole('heading', { name: 'Log in to the Observation Hive' }),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Join with the invite/ })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Join the Observation Hive' })).toBeNull();
  });

  it("logs in with the remembered device's passkey and the password", async () => {
    const fake = scripted('', 'dev_old');
    fake.respond('POST /v1/auth/challenge', 200, {
      device_id: 'dev_old',
      nonce: 'n',
      expires_at: '2030-01-01T00:00:00Z',
      passkey_options: { challenge: 'abc' },
    });
    fake.respond('POST /v1/auth/login', 201, {
      device_id: 'dev_old',
      token: 'tok',
      listener: 'remote',
      expires_at: '2030-01-01T00:00:00Z',
      needs_step_up: false,
    });
    fake.respond('GET /v1/summary', 200, {});
    render(<App browser={fake.browser} />);
    await waitFor(() =>
      expect((screen.getByLabelText('Device') as HTMLInputElement).value).toBe('dev_old'),
    );
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() => expect(posts(fake, '/v1/auth/login')).toHaveLength(1));
    expect(fake.passkeyOptions).toEqual([{ challenge: 'abc' }]);
    expect(bodyOf(posts(fake, '/v1/auth/login')[0]!)).toMatchObject({
      device_id: 'dev_old',
      password: 'pw',
    });
  });
});
