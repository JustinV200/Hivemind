/**
 * Tests for the image intake call of the HTTP client.
 *
 * A shared image is sent as the raw body of `POST /v1/nectar/images` under its own media type, signed
 * like every other request, and the route is on the write list.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises `postImage` in client/http.ts and write_guard.ts.
 *
 * Key invariants:
 *   - The bytes sent are the bytes given, with `Content-Type` set to the image's type.
 *   - The request is signed (the route needs `entrance:submit`).
 *
 * See Also:
 *   - docs/adr/0047 ("Shared images need an image intake route") and tests/shell/write_routes.test.ts.
 */
import { describe, expect, it } from 'vitest';
import { createLandingBoard } from '../../../../../src/landing_board/client';
import { isWriteAllowed } from '../../../../../src/landing_board/client/write_guard';
import { createFakeBrowser } from '../../../../support/fake_browser';
import { storeSession } from '../../../../support/session_fixture';

describe('postImage', () => {
  it('posts the raw bytes under the image media type, signed', async () => {
    const fake = createFakeBrowser();
    await storeSession(fake.browser);
    fake.respond('POST /v1/nectar/images', 201, { id: 'nectar_1' });
    const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
    await board.session.restore();
    await board.http.postImage({ bytes: Uint8Array.from([7, 8, 9]), mediaType: 'image/png' });
    const sent = fake.requests.at(-1)!;
    expect(sent.method).toBe('POST');
    expect(sent.target).toBe('/v1/nectar/images');
    expect(sent.headers['Content-Type']).toBe('image/png');
    expect(sent.headers.Authorization).toBeDefined();
    expect([...sent.body!]).toEqual([7, 8, 9]);
  });

  it('is on the write list', () => {
    expect(isWriteAllowed('/v1/nectar/images')).toBe(true);
    expect(isWriteAllowed('/v1/nectar/other')).toBe(false);
  });
});
