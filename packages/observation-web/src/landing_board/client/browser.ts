/**
 * The `Browser` interface: every platform handle the Landing Board client needs, injected.
 *
 * The client signs requests, opens sockets, stores a key, asks for a passkey and registers for
 * push. Each of those touches a browser global (`fetch`, `WebSocket`, `crypto.subtle`,
 * IndexedDB, `navigator.credentials`, `PushManager`). Handing them in through one small
 * interface means the signing, session and reconnect logic is tested with plain fakes (no
 * mocking library) and the same code runs unchanged in a page, a PWA and the Capacitor APK, whose
 * differences live in `platform/`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Implemented for real by
 *   `platform/real_browser.ts` and for tests by `tests/support/fake_browser.ts`. Everything else
 *   in this folder receives a `Browser` and never reaches for a global.
 *
 * Key invariants:
 *   - Only `src/landing_board/client/` may touch `fetch` or `WebSocket`; this interface is how
 *     they are reached from tests and from the one real binding.
 *   - Time and timers are part of the interface so reconnect backoff is tested without waiting.
 *
 * See Also:
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for the one-client-module rule.
 */

// Bytes backed by a plain ArrayBuffer, which is what WebCrypto and fetch accept.
export type Bytes = Uint8Array<ArrayBuffer>;

// The WebCrypto calls the client makes, narrowed so a fake need implement only these four.
export interface SubtleLike {
  generateKey(
    algorithm: EcKeyGenParams,
    extractable: boolean,
    usages: KeyUsage[],
  ): Promise<CryptoKeyPair>;
  sign(algorithm: EcdsaParams, key: CryptoKey, data: Bytes): Promise<ArrayBuffer>;
  exportKey(format: 'raw', key: CryptoKey): Promise<ArrayBuffer>;
  digest(algorithm: 'SHA-256', data: Bytes): Promise<ArrayBuffer>;
}

// A WebSocket reduced to what the stream wrapper uses; handlers are assigned, not subscribed.
export interface SocketLike {
  onOpen: (() => void) | null;
  onMessage: ((data: string) => void) | null;
  onClose: ((code: number) => void) | null;
  send(data: string): void;
  close(code: number): void;
}

// Where the binding key and session token live (IndexedDB for real, a Map in tests).
export interface KeyValueStore {
  get(key: string): Promise<unknown>;
  put(key: string, value: unknown): Promise<void>;
  remove(key: string): Promise<void>;
}

// The member names are those of PushSubscriptionJSON, which the browser hands over.
export interface WebPushSubscription {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

export interface Browser {
  // The Entrance's origin, e.g. `https://hive.example.ts.net`; request paths are appended to it.
  origin: string;
  fetch(url: string, init: RequestInit): Promise<Response>;
  openSocket(url: string): SocketLike;
  subtle: SubtleLike;
  randomBytes(length: number): Bytes;
  nowMs(): number;
  setTimer(callback: () => void, ms: number): number;
  clearTimer(id: number): void;
  store: KeyValueStore;
  // Runs navigator.credentials.get with a challenge's `passkey_options`; returns the assertion JSON.
  assertPasskey(options: Record<string, unknown>): Promise<Record<string, unknown>>;
  // Asks permission and subscribes this browser to Web Push under the Hive's application key.
  subscribeWebPush(applicationServerKey: Bytes): Promise<WebPushSubscription>;
}
