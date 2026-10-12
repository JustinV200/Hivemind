/**
 * Tests for `HttpClient.postAudio`: the one request whose body is not JSON.
 *
 * A clip goes to `POST /v1/chat/audio` as raw bytes, labelled by the clip's own media type, with
 * the intent and the clip's length in the query. The signature must cover the SHA-256 of exactly
 * the bytes sent and the target exactly as sent (the query included, with `answer:` encoded), and
 * the usual refusals (a clip too long, a refused step-up) must become `LandingBoardError`s.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/http.ts (`postAudio`).
 *
 * Key invariants:
 *   - Signatures are verified with the real public key, as in http.test.ts.
 *   - The body is compared byte for byte, not by length.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.4.
 *   - tests/landing_board/client/http.test.ts for JSON calls.
 */
import { describe, expect, it } from 'vitest';
import type { RecordedClip } from '../../../src/landing_board/client/browser';
import { LandingBoardError } from '../../../src/landing_board/client/errors';
import { createHttp } from '../../../src/landing_board/client/http';
import { createHolder } from '../../../src/landing_board/client/keys';
import { requestString, sha256Hex } from '../../../src/landing_board/client/signing';
import { createFakeBrowser } from '../../support/fake_browser';
import { makeSession, verifies } from '../../support/session_fixture';

const ACCEPTED = { transcript: 'hello', intent: 'chat', duration_s: 2, language: null };
const ID = 'msg_01M3AN9216QQX5JVNQHHWBFJR9';

function clip(over: Partial<RecordedClip> = {}): RecordedClip {
  return {
    bytes: new Uint8Array([0, 255, 128, 7, 7, 7, 1]),
    mediaType: 'audio/webm',
    durationS: 2.456,
    ...over,
  };
}

async function client() {
  const fake = createFakeBrowser();
  const holder = createHolder();
  const session = await makeSession();
  holder.set(session);
  const http = createHttp({ browser: fake.browser, holder, onUnauthorized: () => {} });
  return { fake, http, session };
}

describe('postAudio', () => {
  it('sends the exact bytes under the clip media type, and signs their hash', async () => {
    const { fake, http, session } = await client();
    fake.respond('POST /v1/chat/audio', 202, ACCEPTED);
    const sent = clip();
    const answer = await http.postAudio(sent, 'goal');

    expect(answer.transcript).toBe('hello');
    const request = fake.requests[0]!;
    expect(request.method).toBe('POST');
    expect(request.target).toBe('/v1/chat/audio?intent=goal&duration_s=2.46');
    expect(request.headers['Content-Type']).toBe('audio/webm');
    expect(Array.from(request.body!)).toEqual(Array.from(sent.bytes));
    const signed = requestString({
      method: 'POST',
      target: request.target,
      timestamp: Number(request.headers['X-Hive-Timestamp']),
      nonce: request.headers['X-Hive-Nonce']!,
      bodySha256: await sha256Hex(crypto.subtle, sent.bytes),
    });
    expect(await verifies(session.publicKey, request.headers['X-Hive-Signature']!, signed)).toBe(
      true,
    );
  });

  it('labels each format by its own type', async () => {
    const { fake, http } = await client();
    fake.respond('POST /v1/chat/audio', 202, ACCEPTED);
    await http.postAudio(clip({ mediaType: 'audio/mp4' }), 'chat');
    await http.postAudio(clip({ mediaType: 'audio/wav' }), 'chat');
    expect(fake.requests.map((r) => r.headers['Content-Type'])).toEqual(['audio/mp4', 'audio/wav']);
  });

  it('encodes an answer intent in the signed target', async () => {
    const { fake, http, session } = await client();
    fake.respond('POST /v1/chat/audio', 202, ACCEPTED);
    await http.postAudio(clip(), `answer:${ID}`);
    const request = fake.requests[0]!;
    expect(request.target).toBe(`/v1/chat/audio?intent=answer%3A${ID}&duration_s=2.46`);
    const signed = requestString({
      method: 'POST',
      target: request.target,
      timestamp: Number(request.headers['X-Hive-Timestamp']),
      nonce: request.headers['X-Hive-Nonce']!,
      bodySha256: await sha256Hex(crypto.subtle, request.body!),
    });
    expect(await verifies(session.publicKey, request.headers['X-Hive-Signature']!, signed)).toBe(
      true,
    );
  });

  it('never states a length of zero', async () => {
    const { fake, http } = await client();
    fake.respond('POST /v1/chat/audio', 202, ACCEPTED);
    await http.postAudio(clip({ durationS: 0 }), 'chat');
    expect(fake.requests[0]!.target).toBe('/v1/chat/audio?intent=chat&duration_s=0.01');
  });

  it('turns a refusal into a LandingBoardError carrying its status and code', async () => {
    const { fake, http } = await client();
    fake.respond('POST /v1/chat/audio', 413, {
      error: 'hivemind.entrance.clip_refused',
      detail: 'too long',
    });
    const failure = await http.postAudio(clip(), 'goal').catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(LandingBoardError);
    expect((failure as LandingBoardError).status).toBe(413);
    expect((failure as LandingBoardError).code).toBe('hivemind.entrance.clip_refused');
  });
});

describe('recorder', () => {
  it('is the browser recorder when it has one, and null when it has none', async () => {
    const holder = createHolder();
    const plain = createFakeBrowser();
    expect(
      createHttp({ browser: plain.browser, holder, onUnauthorized: () => {} }).recorder,
    ).toBeNull();
    const recorder = { start: () => Promise.reject(new Error('unused')) };
    const withMic = createFakeBrowser(undefined, { recorder });
    expect(
      createHttp({ browser: withMic.browser, holder, onUnauthorized: () => {} }).recorder,
    ).toBe(recorder);
  });
});
