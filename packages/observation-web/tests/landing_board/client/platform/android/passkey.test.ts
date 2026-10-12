/**
 * Tests for the Android passkey adapter.
 *
 * The native `Passkey` plugin takes WebAuthn options as a JSON string and answers with a JSON
 * string. The adapter must send exactly the Entrance's options, parse the answer back into the
 * object `LoginRequest` and enrolment expect, and turn every plugin failure into one error that the
 * session client reports as a cancelled passkey rather than a network failure.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/android/passkey.ts and, through the
 *   session client, the cancelled-passkey code of errors.ts.
 *
 * Key invariants:
 *   - The options reach the plugin unchanged; the answer is returned unchanged.
 *   - A rejection with any code, or an unreadable answer, is a `PasskeyError`.
 *
 * See Also:
 *   - src/landing_board/client/platform/android/passkey.ts.
 */
import { describe, expect, it } from 'vitest';
import { createLandingBoard, LandingBoardError } from '../../../../../src/landing_board/client';
import { CLIENT_CODES } from '../../../../../src/landing_board/client/errors';
import {
  androidPasskeys,
  PasskeyError,
} from '../../../../../src/landing_board/client/platform/android/passkey';
import type { PasskeyPlugin } from '../../../../../src/landing_board/client/platform/android/plugins';
import { createFakeBrowser } from '../../../../support/fake_browser';

const OPTIONS = { challenge: 'Y2hhbGxlbmdl', rpId: 'hive.test', allowCredentials: [] };

class FakePasskey implements PasskeyPlugin {
  calls: { method: string; optionsJson: string }[] = [];
  answer: unknown = { json: '{"id":"cred-1","type":"public-key","response":{}}' };

  private respond(method: string, options: { optionsJson: string }): Promise<{ json: string }> {
    this.calls.push({ method, optionsJson: options.optionsJson });
    if (this.answer instanceof Error || (this.answer as { code?: string }).code) {
      return Promise.reject(this.answer);
    }
    return Promise.resolve(this.answer as { json: string });
  }

  get = (options: { optionsJson: string }) => this.respond('get', options);
  create = (options: { optionsJson: string }) => this.respond('create', options);
}

describe('android passkeys', () => {
  it('sends the options as JSON and returns the parsed assertion', async () => {
    const plugin = new FakePasskey();
    const result = await androidPasskeys(plugin).assertPasskey(OPTIONS);
    expect(plugin.calls).toEqual([{ method: 'get', optionsJson: JSON.stringify(OPTIONS) }]);
    expect(result).toEqual({ id: 'cred-1', type: 'public-key', response: {} });
  });

  it('creates a passkey through the plugin for enrolment', async () => {
    const plugin = new FakePasskey();
    plugin.answer = { json: '{"id":"new","type":"public-key","response":{"attestationObject":"a"}}' };
    const result = await androidPasskeys(plugin).createPasskey({ challenge: 'AQID' });
    expect(plugin.calls[0]).toEqual({ method: 'create', optionsJson: '{"challenge":"AQID"}' });
    expect(result.id).toBe('new');
  });

  it.each(['CANCELLED', 'NO_CREDENTIAL', 'UNSUPPORTED', 'FAILED'] as const)(
    'maps a %s rejection to a PasskeyError with that code',
    async (code) => {
      const plugin = new FakePasskey();
      plugin.answer = Object.assign(new Error('native detail'), { code });
      const failure = androidPasskeys(plugin).assertPasskey(OPTIONS);
      await expect(failure).rejects.toBeInstanceOf(PasskeyError);
      await expect(failure).rejects.toMatchObject({ code });
    },
  );

  it('treats an unknown rejection code and unreadable answers as FAILED', async () => {
    const plugin = new FakePasskey();
    plugin.answer = Object.assign(new Error('x'), { code: 'SURPRISE' });
    await expect(androidPasskeys(plugin).assertPasskey(OPTIONS)).rejects.toMatchObject({
      code: 'FAILED',
    });
    plugin.answer = { json: 'not json' };
    await expect(androidPasskeys(plugin).createPasskey(OPTIONS)).rejects.toMatchObject({
      code: 'FAILED',
    });
    plugin.answer = { json: '[1]' };
    await expect(androidPasskeys(plugin).createPasskey(OPTIONS)).rejects.toMatchObject({
      code: 'FAILED',
    });
  });

  it('does not leak the native message', async () => {
    const plugin = new FakePasskey();
    plugin.answer = Object.assign(new Error('secret native detail'), { code: 'CANCELLED' });
    const failure = await androidPasskeys(plugin)
      .assertPasskey(OPTIONS)
      .catch((error: unknown) => error as Error);
    expect(failure.message).not.toContain('secret');
  });

  it('is reported by the session client as a cancelled passkey, not a network failure', async () => {
    const plugin = new FakePasskey();
    plugin.answer = Object.assign(new Error('x'), { code: 'CANCELLED' });
    const fake = createFakeBrowser();
    fake.browser.assertPasskey = androidPasskeys(plugin).assertPasskey;
    fake.respond('POST /v1/auth/challenge', 200, {
      nonce: 'n',
      expires_at: '2030-01-01T00:00:00Z',
      passkey_options: OPTIONS,
    });
    const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
    const failure = await board.session.login('dev_1', 'pw').catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(LandingBoardError);
    expect((failure as LandingBoardError).code).toBe(CLIENT_CODES.passkeyCancelled);
  });
});
