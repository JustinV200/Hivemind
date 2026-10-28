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
 *   - docs/adr/0049-observation-hive-dashboard-stack.md for the one-client-module rule.
 */
import type { OperationOf } from '../call_types';

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

// The clip formats `POST /v1/chat/audio` takes, read off the generated request body so a format
// the document drops stops compiling here.
export type AudioMediaType = keyof OperationOf<'/v1/chat/audio', 'post'>['requestBody']['content'] &
  string;

// One finished recording: the encoded bytes, their format and how long the human spoke.
export interface RecordedClip {
  bytes: Bytes;
  mediaType: AudioMediaType;
  durationS: number;
}

// A recording in progress. `stop` resolves with the clip; `cancel` throws the audio away.
export interface ActiveRecording {
  stop(): Promise<RecordedClip>;
  cancel(): void;
}

// The browser's recorder (MediaRecorder over the microphone). `start` asks for the microphone, so
// it rejects when the human refuses; a device with no recorder simply has no `AudioRecorder`.
export interface AudioRecorder {
  start(): Promise<ActiveRecording>;
}

// One item another app shared into the Hive, before the human has confirmed sending it. Text is a
// string; an image or a clip is raw bytes under the media type the sharing app declared.
export interface SharedFile {
  bytes: Bytes;
  mediaType: string;
}

export type SharedItem =
  | { kind: 'text'; text: string }
  | ({ kind: 'image' } & SharedFile)
  | ({ kind: 'audio' } & SharedFile)
  // Something the phone could not take (too large, unreadable); the reason is a short code.
  | { kind: 'refused'; reason: string };

// The phone's share sheet. `onShared` delivers each shared item (one that arrived before anyone
// listened is delivered first) and returns the function that stops listening.
export interface ShareIntake {
  onShared(listener: (item: SharedItem) => void): () => void;
}

// The ongoing notification shown while questions wait; `clear` withdraws it.
export interface WaitingNotifier {
  show(count: number): Promise<void>;
  clear(): Promise<void>;
}

// How a phone learns the Entrance's address: from an invite link opened in the app or pasted by
// hand. `configured` is false until one address has been stored; `accept` adopts a link's origin
// and reloads on its join screen, and resolves false for text that is not an invite link.
export interface InviteLinks {
  configured: boolean;
  accept(link: string): Promise<boolean>;
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
  // Runs navigator.credentials.create with an invite's creation options; returns the registration JSON.
  createPasskey(options: Record<string, unknown>): Promise<Record<string, unknown>>;
  // Asks permission and subscribes this browser to Web Push under the Hive's application key.
  subscribeWebPush(applicationServerKey: Bytes): Promise<WebPushSubscription>;
  // Gives the push distributor's registration back; absent where the browser owns the subscription.
  unsubscribeWebPush?: () => Promise<void>;
  // Present only in the Android shell (ADR-0050): the share sheet, the waiting notification and the
  // invite link intake. A browser carries none of them and the views that use them render nothing.
  shareIntake?: ShareIntake;
  waitingNotifier?: WaitingNotifier;
  inviteLinks?: InviteLinks;
  // Absent on a device that cannot record (no microphone API); a view hides push-to-talk then.
  recorder?: AudioRecorder;
  // The stand key this tab took from the Hive Stand browser link (ADR-0068), sent as
  // `X-Hive-Stand`; absent or null everywhere else, which is every device but the Hive Stand.
  standKey?: () => string | null;
}
