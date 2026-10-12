/**
 * The session binding key and the stored session: generate, save, load, sign, delete.
 *
 * A browser session is bound to a WebCrypto ECDSA P-256 key (ADR-0041, ADR-0046). The key is made
 * non-extractable, so script running in the page, a copied token or a copied IndexedDB record can
 * hold it but never read it out, and only the public point goes to the Entrance (at the login
 * challenge). The token and the key live together in the injected store and are deleted together
 * at logout. The device id is remembered separately as a convenience so the login screen can
 * prefill it; it is not a secret.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Used by `session.ts` (generate, save,
 *   load, clear) and by `index.ts` (the in-memory holder `http.ts` and `socket.ts` sign with).
 *
 * Key invariants:
 *   - The private key is generated with `extractable: false`.
 *   - A signature is WebCrypto's raw IEEE P1363 `r || s` (64 bytes), base64url with no padding;
 *     no DER conversion is wanted or done.
 *   - A stored value that fails its shape check is treated as absent, never half-trusted.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 5 (the binding key) and section 6 (signatures).
 *   - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md.
 */
import type { KeyValueStore, SubtleLike } from './browser';
import { base64urlEncode } from './signing';

const SESSION_RECORD = 'session';
const DEVICE_RECORD = 'device';

export interface BindingKey {
  privateKey: CryptoKey;
  // The uncompressed P-256 point, base64url, as sent in `binding_key`.
  publicKey: string;
}

export interface StoredSession extends BindingKey {
  deviceId: string;
  token: string;
  listener: string;
  expiresAt: string;
  needsStepUp: boolean;
}

export async function generateBindingKey(subtle: SubtleLike): Promise<BindingKey> {
  const pair = await subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  // Public keys are always exportable, even when the private half is not.
  const point = new Uint8Array(await subtle.exportKey('raw', pair.publicKey));
  return { privateKey: pair.privateKey, publicKey: base64urlEncode(point) };
}

export async function signWith(subtle: SubtleLike, key: CryptoKey, text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const signature = await subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, data);
  return base64urlEncode(new Uint8Array(signature));
}

function isStoredSession(value: unknown): value is StoredSession {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.deviceId === 'string' &&
    typeof record.token === 'string' &&
    typeof record.listener === 'string' &&
    typeof record.expiresAt === 'string' &&
    typeof record.needsStepUp === 'boolean' &&
    typeof record.publicKey === 'string' &&
    typeof record.privateKey === 'object' &&
    record.privateKey !== null
  );
}

export async function saveSession(store: KeyValueStore, session: StoredSession): Promise<void> {
  await store.put(SESSION_RECORD, session);
  await store.put(DEVICE_RECORD, session.deviceId);
}

export async function loadSession(store: KeyValueStore): Promise<StoredSession | null> {
  const value = await store.get(SESSION_RECORD);
  return isStoredSession(value) ? value : null;
}

// Logout: the key and token go; the remembered device id stays for the next login.
export async function clearSession(store: KeyValueStore): Promise<void> {
  await store.remove(SESSION_RECORD);
}

export async function recallDevice(store: KeyValueStore): Promise<string | null> {
  const value = await store.get(DEVICE_RECORD);
  return typeof value === 'string' ? value : null;
}

// The live session, held in memory so every request can be signed without touching the store.
export interface SessionHolder {
  get(): StoredSession | null;
  set(session: StoredSession | null): void;
}

export function createHolder(): SessionHolder {
  let current: StoredSession | null = null;
  return {
    get: () => current,
    set: (session) => {
      current = session;
    },
  };
}
