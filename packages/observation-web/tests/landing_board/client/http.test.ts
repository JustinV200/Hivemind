/**
 * Tests for the HTTP wrapper: signing, the write list, error mapping and 401 handling.
 *
 * Every request must carry four headers whose signature verifies under the session's key over
 * the exact bytes sent. A mutating request outside the allow-list must be refused before it
 * reaches `fetch`. Refusals, bare or not, must become `LandingBoardError`s, and a 401 on a signed
 * request must tell the owner the session is over.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/http.ts.
 *
 * Key invariants:
 *   - Signatures are verified with the real public key, not compared to a stored string.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 6.
 */
import { describe, expect, it } from 'vitest';
import { createHttp } from '../../../src/landing_board/client/http';
import { LandingBoardError } from '../../../src/landing_board/client/errors';
import { createHolder } from '../../../src/landing_board/client/keys';
import {
  EMPTY_BODY_SHA256,
  requestString,
  sha256Hex,
} from '../../../src/landing_board/client/signing';
import { createFakeBrowser } from '../../support/fake_browser';
import { makeSession, verifies } from '../../support/session_fixture';

async function signedClient() {
  const fake = createFakeBrowser();
  const holder = createHolder();
  const session = await makeSession();
  holder.set(session);
  let lost = 0;
  const http = createHttp({ browser: fake.browser, holder, onUnauthorized: () => (lost += 1) });
  return { fake, http, session, lost: () => lost };
}

describe('signing', () => {
  it('signs a GET with the empty-body hash and the four headers', async () => {
    const { fake, http, session } = await signedClient();
    fake.respond('GET /v1/cells', 200, { cells: [] });
    await http.call('get', '/v1/cells');
    const request = fake.requests[0]!;
    expect(request.headers.Authorization).toBe(`Bearer ${session.token}`);
    expect(request.headers['X-Hive-Timestamp']).toBe(String(Math.floor(fake.now() / 1000)));
    const signed = requestString({
      method: 'GET',
      target: '/v1/cells',
      timestamp: Number(request.headers['X-Hive-Timestamp']),
      nonce: request.headers['X-Hive-Nonce']!,
      bodySha256: EMPTY_BODY_SHA256,
    });
    expect(await verifies(session.publicKey, request.headers['X-Hive-Signature']!, signed)).toBe(
      true,
    );
  });

  it('hashes the exact body bytes it sends and signs the query as sent', async () => {
    const { fake, http, session } = await signedClient();
    fake.respond('POST /v1/chat', 202, { id: 'msg_1' });
    await http.call('post', '/v1/chat', { body: { text: 'hello é' } });
    const request = fake.requests[0]!;
    expect(new TextDecoder().decode(request.body!)).toBe('{"text":"hello é"}');
    const signed = requestString({
      method: 'POST',
      target: '/v1/chat',
      timestamp: Number(request.headers['X-Hive-Timestamp']),
      nonce: request.headers['X-Hive-Nonce']!,
      bodySha256: await sha256Hex(crypto.subtle, request.body!),
    });
    expect(await verifies(session.publicKey, request.headers['X-Hive-Signature']!, signed)).toBe(
      true,
    );
  });

  it('puts query parameters in the target and skips empty ones', async () => {
    const { fake, http } = await signedClient();
    fake.respond('GET /v1/chat', 200, { entries: [], newest_seq: null });
    await http.call('get', '/v1/chat', { query: { limit: 20, after: null } });
    expect(fake.requests[0]!.target).toBe('/v1/chat?limit=20');
  });

  it('fills and percent-encodes path parameters', async () => {
    const { fake, http } = await signedClient();
    fake.respond('POST /v1/inbox/questions/q%2F1/answer', 200, { question_status: 'ANSWERED' });
    await http.call('post', '/v1/inbox/questions/{question_id}/answer', {
      params: { question_id: 'q/1' },
      body: { text: 'spring' },
    });
    expect(fake.requests[0]!.target).toBe('/v1/inbox/questions/q%2F1/answer');
  });

  it('sends nothing signed when there is no session', async () => {
    const fake = createFakeBrowser();
    fake.respond('POST /v1/auth/challenge', 200, { device_id: 'd', nonce: 'n', expires_at: 'x' });
    const http = createHttp({
      browser: fake.browser,
      holder: createHolder(),
      onUnauthorized: () => {},
    });
    await http.call('post', '/v1/auth/challenge', { body: { device_id: 'd' } });
    expect(fake.requests[0]!.headers.Authorization).toBeUndefined();
  });
});

describe('write list', () => {
  it('refuses a mutating request outside the list before anything is sent', async () => {
    const { fake, http } = await signedClient();
    await expect(
      http.call('post', '/v1/cells/{cell_id}/isolate', {
        params: { cell_id: 'c' },
        body: { reason: 'x' } as never,
      }),
    ).rejects.toMatchObject({
      code: 'hivemind.client.write_refused',
    });
    expect(fake.requests).toHaveLength(0);
  });
});

describe('errors', () => {
  it('maps an ErrorBody to a typed error', async () => {
    const { fake, http } = await signedClient();
    fake.respond('GET /v1/cells', 403, {
      error: 'hivemind.entrance.step_up_required',
      detail: 'Step up first.',
      reason: 'new_network',
    });
    const failure = await http.call('get', '/v1/cells').catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(LandingBoardError);
    expect(failure).toMatchObject({ status: 403, reason: 'new_network' });
  });

  it('maps a bare refusal with no ErrorBody to http_refused', async () => {
    const { fake, http } = await signedClient();
    fake.respond('GET /v1/cells', 429);
    await expect(http.call('get', '/v1/cells')).rejects.toMatchObject({
      status: 429,
      code: 'hivemind.entrance.http_refused',
    });
  });

  it('maps a failed fetch to the network code', async () => {
    const { fake, http } = await signedClient();
    fake.browser.fetch = () => Promise.reject(new TypeError('offline'));
    await expect(http.call('get', '/v1/cells')).rejects.toMatchObject({
      code: 'hivemind.client.network',
    });
  });

  it('tells the owner the session is over on a 401 to a signed request', async () => {
    const { fake, http, lost } = await signedClient();
    fake.respond('GET /v1/cells', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'no',
    });
    await expect(http.call('get', '/v1/cells')).rejects.toMatchObject({ status: 401 });
    expect(lost()).toBe(1);
  });

  it('does not report a lost session for a 401 on login (there is none)', async () => {
    const fake = createFakeBrowser();
    let lost = 0;
    const http = createHttp({
      browser: fake.browser,
      holder: createHolder(),
      onUnauthorized: () => (lost += 1),
    });
    fake.respond('POST /v1/auth/challenge', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'no',
    });
    await expect(
      http.call('post', '/v1/auth/challenge', { body: { device_id: 'd' } }),
    ).rejects.toBeInstanceOf(LandingBoardError);
    expect(lost).toBe(0);
  });

  it('returns undefined for a 204', async () => {
    const { fake, http } = await signedClient();
    fake.respond('POST /v1/auth/logout', 204);
    await expect(http.call('post', '/v1/auth/logout')).resolves.toBeUndefined();
  });
});
