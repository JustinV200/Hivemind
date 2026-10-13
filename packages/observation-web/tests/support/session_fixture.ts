/**
 * Test helpers: a logged-in session with a real P-256 key, and signature verification.
 *
 * Many tests need a session that already exists (a socket test, a shell test) and need to prove
 * that what the client sent was signed with that session's key. These helpers make the key with
 * the real WebCrypto, store the session the way `keys.ts` does, and verify a base64url signature
 * against the public point, so a test asserts on the cryptography and not on a stand-in.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the http, socket, session, shell and app tests.
 *
 * Key invariants:
 *   - Keys are generated non-extractable, as in production.
 *   - Verification imports the public point from the stored base64url, as the Entrance does.
 *
 * See Also:
 *   - src/landing_board/client/keys.ts for the production counterpart.
 *   - tests/support/fake_browser.ts for the fake browser these helpers pair with.
 */
import type { Browser } from '../../src/landing_board/client/browser';
import { generateBindingKey, saveSession } from '../../src/landing_board/client/keys';
import type { StoredSession } from '../../src/landing_board/client/keys';
import { base64urlDecode } from '../../src/landing_board/client/signing';

export async function makeSession(overrides: Partial<StoredSession> = {}): Promise<StoredSession> {
  const key = await generateBindingKey(crypto.subtle);
  return {
    ...key,
    deviceId: 'dev_test',
    token: 'token-123',
    listener: 'remote',
    expiresAt: '2030-01-01T00:00:00Z',
    needsStepUp: false,
    ...overrides,
  };
}

export async function storeSession(
  browser: Browser,
  overrides: Partial<StoredSession> = {},
): Promise<StoredSession> {
  const session = await makeSession(overrides);
  await saveSession(browser.store, session);
  return session;
}

export async function verifies(
  publicKey: string,
  signature: string,
  text: string,
): Promise<boolean> {
  const key = await crypto.subtle.importKey(
    'raw',
    base64urlDecode(publicKey),
    { name: 'ECDSA', namedCurve: 'P-256' },
    false,
    ['verify'],
  );
  return crypto.subtle.verify(
    { name: 'ECDSA', hash: 'SHA-256' },
    key,
    base64urlDecode(signature),
    new TextEncoder().encode(text),
  );
}
