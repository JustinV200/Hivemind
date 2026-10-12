/**
 * Tests for the binding key and its storage.
 *
 * The key must be non-extractable, its signature must be the raw 64-byte `r || s` form that the
 * Entrance verifies, and the stored session must reload intact and vanish at logout. The first
 * two use the real WebCrypto; storage uses a fake `SubtleCrypto` and an in-memory store, which
 * also proves `keys.ts` depends only on the injected handles.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/keys.ts.
 *
 * Key invariants:
 *   - Nothing here reads the private key's bytes: it cannot, by construction.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 5 for the binding key.
 */
import { describe, expect, it } from 'vitest';
import type { SubtleLike } from '../../../src/landing_board/client/browser';
import {
  clearSession,
  createHolder,
  generateBindingKey,
  loadSession,
  recallDevice,
  saveSession,
  signWith,
} from '../../../src/landing_board/client/keys';
import { base64urlDecode } from '../../../src/landing_board/client/signing';
import { createFakeBrowser } from '../../support/fake_browser';
import { makeSession, verifies } from '../../support/session_fixture';

// Records how the key was asked for; returns inert key objects.
function fakeSubtle(calls: unknown[][]): SubtleLike {
  const key = (name: string) => ({ name }) as unknown as CryptoKey;
  return {
    generateKey: (algorithm, extractable, usages) => {
      calls.push([algorithm, extractable, usages]);
      return Promise.resolve({ privateKey: key('private'), publicKey: key('public') });
    },
    exportKey: () => Promise.resolve(Uint8Array.from([4, ...new Array(64).fill(7)]).buffer),
    sign: () => Promise.resolve(new Uint8Array(64).buffer),
    digest: () => Promise.resolve(new Uint8Array(32).buffer),
  };
}

describe('generateBindingKey', () => {
  it('asks for a non-extractable P-256 signing key and exports only the public point', async () => {
    const calls: unknown[][] = [];
    const key = await generateBindingKey(fakeSubtle(calls));
    expect(calls).toEqual([[{ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']]]);
    // An uncompressed P-256 point is 65 bytes: 0x04 then x and y.
    expect(base64urlDecode(key.publicKey)).toHaveLength(65);
  });
});

describe('signWith', () => {
  it('makes a 64-byte r||s signature that verifies under the public point', async () => {
    const key = await generateBindingKey(crypto.subtle);
    const signature = await signWith(crypto.subtle, key.privateKey, 'hive-ws-v1\n/p\n1\nn');
    expect(base64urlDecode(signature)).toHaveLength(64);
    expect(await verifies(key.publicKey, signature, 'hive-ws-v1\n/p\n1\nn')).toBe(true);
    expect(await verifies(key.publicKey, signature, 'something else')).toBe(false);
  });

  it('cannot export the private key', async () => {
    const key = await generateBindingKey(crypto.subtle);
    await expect(crypto.subtle.exportKey('pkcs8', key.privateKey)).rejects.toThrow();
  });
});

describe('stored session', () => {
  it('is stored and reloaded with its key and token', async () => {
    const { store } = createFakeBrowser();
    const session = await makeSession({ token: 'abc' });
    await saveSession(store, session);
    const loaded = await loadSession(store);
    expect(loaded?.token).toBe('abc');
    // The very same CryptoKey comes back: it is never serialised or copied.
    expect(loaded?.privateKey).toBe(session.privateKey);
  });

  it('is deleted at logout while the device id stays for the next login', async () => {
    const { store } = createFakeBrowser();
    await saveSession(store, await makeSession({ deviceId: 'dev_x' }));
    await clearSession(store);
    expect(await loadSession(store)).toBeNull();
    expect(await recallDevice(store)).toBe('dev_x');
  });

  it('treats a malformed stored value as absent', async () => {
    const { store } = createFakeBrowser();
    await store.put('session', { token: 'only a token' });
    expect(await loadSession(store)).toBeNull();
  });
});

describe('holder', () => {
  it('holds the live session in memory', async () => {
    const holder = createHolder();
    expect(holder.get()).toBeNull();
    const session = await makeSession();
    holder.set(session);
    expect(holder.get()).toBe(session);
  });
});
