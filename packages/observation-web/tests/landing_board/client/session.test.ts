/**
 * Tests for login, restore, step-up and logout against a scripted Entrance.
 *
 * The happy path is the whole ceremony of landing-board.md section 5: a challenge that carries
 * the binding key, a passkey assertion, then a login that repeats the key and adds the password.
 * After it, requests must be signed. Failures must map to the fixed error table, and logout must
 * delete the key even when the Entrance cannot be reached.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/session.ts through the
 *   real `createLandingBoard`, so the wiring in index.ts is covered too.
 *
 * Key invariants:
 *   - The fake Entrance records requests; assertions are on what was sent, not on internals.
 *
 * See Also:
 *   - docs/entrance/landing-board.md sections 5 and 7.
 */
import { describe, expect, it } from 'vitest';
import { createLandingBoard, describeError } from '../../../src/landing_board/client';
import { loadSession } from '../../../src/landing_board/client/keys';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

const CHALLENGE = {
  device_id: 'dev_1',
  nonce: 'challenge-nonce',
  expires_at: '2026-01-01T00:01:00Z',
  passkey_options: { challenge: 'Y2hhbGxlbmdl', rpId: 'hive.test' },
};
const OPENED = {
  device_id: 'dev_1',
  token: 'tok-1',
  listener: 'loopback',
  expires_at: '2030-01-01T00:00:00Z',
  needs_step_up: false,
};

function scripted(): {
  fake: FakeBrowser;
  board: ReturnType<typeof createLandingBoard>;
  lost: () => number;
} {
  const fake = createFakeBrowser();
  let lost = 0;
  const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => (lost += 1) });
  fake.respond('POST /v1/auth/challenge', 200, CHALLENGE);
  fake.respond('POST /v1/auth/login', 201, OPENED);
  return { fake, board, lost: () => lost };
}

const bodyOf = (fake: FakeBrowser, index: number): Record<string, unknown> =>
  JSON.parse(new TextDecoder().decode(fake.requests[index]!.body!)) as Record<string, unknown>;

describe('login', () => {
  it('runs challenge, passkey, then login with the same binding key and the password', async () => {
    const { fake, board } = scripted();
    const opened = await board.session.login('dev_1', 'correct horse');
    expect(opened).toEqual({
      deviceId: 'dev_1',
      listener: 'loopback',
      expiresAt: '2030-01-01T00:00:00Z',
      needsStepUp: false,
    });
    const challenge = bodyOf(fake, 0);
    const login = bodyOf(fake, 1);
    expect(challenge.device_id).toBe('dev_1');
    expect(typeof challenge.binding_key).toBe('string');
    // The Entrance binds the session to the key sent with the challenge; login must repeat it.
    expect(login.binding_key).toBe(challenge.binding_key);
    expect(login).toMatchObject({
      device_id: 'dev_1',
      nonce: 'challenge-nonce',
      password: 'correct horse',
    });
    expect(login.assertion).toMatchObject({ id: 'credential' });
    expect(fake.passkeyOptions).toEqual([CHALLENGE.passkey_options]);
    // Neither login request is signed: there is no session yet.
    expect(fake.requests.every((r) => r.headers.Authorization === undefined)).toBe(true);
  });

  it('signs later requests with the new session and stores it', async () => {
    const { fake, board } = scripted();
    await board.session.login('dev_1', 'pw');
    fake.respond('GET /v1/cells', 200, { cells: [] });
    await board.http.call('get', '/v1/cells');
    const headers = fake.requests.at(-1)!.headers;
    expect(headers.Authorization).toBe('Bearer tok-1');
    expect(Object.keys(headers)).toEqual(
      expect.arrayContaining(['X-Hive-Timestamp', 'X-Hive-Nonce', 'X-Hive-Signature']),
    );
    expect((await loadSession(fake.store))?.token).toBe('tok-1');
  });

  it('never stores the password', async () => {
    const { fake, board } = scripted();
    await board.session.login('dev_1', 'secret-pw');
    expect(JSON.stringify(await fake.store.get('session'))).not.toContain('secret-pw');
  });

  it('maps a 401 to the fixed error table without saying which factor failed', async () => {
    const { fake, board } = scripted();
    fake.respond('POST /v1/auth/login', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'Authentication failed.',
    });
    const failure = await board.session.login('dev_1', 'wrong').catch((e: unknown) => e);
    expect(failure).toMatchObject({ status: 401 });
    expect(describeError(failure)).toEqual({
      code: 'hivemind.entrance.authentication_failed',
      sentence: 'The Hive did not accept this login.',
      action: 'Check the device and the password, then log in again.',
    });
    expect(await loadSession(fake.store)).toBeNull();
  });

  it('shows the Entrance detail for a code the table does not know', async () => {
    const { fake, board } = scripted();
    fake.respond('POST /v1/auth/challenge', 400, {
      error: 'hivemind.entrance.surprise',
      detail: 'Something odd.',
    });
    const failure = await board.session.login('dev_1', 'pw').catch((e: unknown) => e);
    expect(describeError(failure)).toEqual({
      code: 'hivemind.entrance.surprise',
      sentence: 'Something odd.',
      action: '',
    });
  });

  it('refuses a device with no passkey options without sending the login', async () => {
    const { fake, board } = scripted();
    fake.respond('POST /v1/auth/challenge', 200, { ...CHALLENGE, passkey_options: null });
    await expect(board.session.login('dev_1', 'pw')).rejects.toMatchObject({
      code: 'hivemind.client.passkey_required',
    });
    expect(fake.requests.map((r) => r.target)).toEqual(['/v1/auth/challenge']);
  });
});

describe('restore', () => {
  it('restores a stored session so requests are signed after a reload', async () => {
    const { fake, board } = scripted();
    await storeSession(fake.browser, { token: 'stored', listener: 'remote' });
    expect(await board.session.restore()).toMatchObject({
      deviceId: 'dev_test',
      listener: 'remote',
    });
    fake.respond('GET /v1/cells', 200, { cells: [] });
    await board.http.call('get', '/v1/cells');
    expect(fake.requests[0]!.headers.Authorization).toBe('Bearer stored');
  });

  it('drops a session past its absolute end', async () => {
    const { fake, board } = scripted();
    await storeSession(fake.browser, { expiresAt: '2020-01-01T00:00:00Z' });
    expect(await board.session.restore()).toBeNull();
    expect(await loadSession(fake.store)).toBeNull();
  });

  it('returns null when nothing is stored', async () => {
    expect(await scripted().board.session.restore()).toBeNull();
  });
});

describe('logout', () => {
  it('ends the session on the Entrance and deletes the key', async () => {
    const { fake, board } = scripted();
    fake.respond('POST /v1/auth/logout', 204);
    await board.session.login('dev_1', 'pw');
    await board.session.logout();
    expect(fake.requests.at(-1)).toMatchObject({ method: 'POST', target: '/v1/auth/logout' });
    expect(fake.requests.at(-1)!.headers.Authorization).toBe('Bearer tok-1');
    expect(await loadSession(fake.store)).toBeNull();
    expect(await board.session.rememberedDevice()).toBe('dev_1');
  });

  it('deletes the key even when the Entrance cannot be reached', async () => {
    const { fake, board } = scripted();
    await board.session.login('dev_1', 'pw');
    fake.browser.fetch = () => Promise.reject(new TypeError('offline'));
    await board.session.logout();
    expect(await loadSession(fake.store)).toBeNull();
  });
});

describe('401 on a signed request', () => {
  it('clears the session and tells the owner', async () => {
    const { fake, board, lost } = scripted();
    await board.session.login('dev_1', 'pw');
    fake.respond('GET /v1/cells', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'no',
    });
    await board.http.call('get', '/v1/cells').catch(() => undefined);
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(lost()).toBe(1);
    expect(await loadSession(fake.store)).toBeNull();
  });
});

describe('stepUp', () => {
  it('answers a fresh challenge with a passkey and the password, then clears the flag', async () => {
    const { fake, board } = scripted();
    fake.respond('POST /v1/auth/login', 201, { ...OPENED, needs_step_up: true });
    fake.respond('POST /v1/auth/step-up/challenge', 200, { ...CHALLENGE, nonce: 'step-nonce' });
    fake.respond('POST /v1/auth/step-up', 200, { stepped_up_until: '2030-01-01T00:05:00Z' });
    const opened = await board.session.login('dev_1', 'pw');
    expect(opened.needsStepUp).toBe(true);
    await board.session.stepUp('pw');
    expect(bodyOf(fake, fake.requests.length - 1)).toMatchObject({
      nonce: 'step-nonce',
      password: 'pw',
    });
    expect((await loadSession(fake.store))?.needsStepUp).toBe(false);
  });
});
