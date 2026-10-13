/**
 * The one `fetch` wrapper: signs every request, enforces the write list, maps refusals.
 *
 * Every call to the Landing Board goes through `call`. When a session is held it adds the four
 * headers of `x-hive-signing` (bearer token, timestamp, nonce, signature over `hive-request-v1`
 * with the body's SHA-256), signing the exact bytes it then sends. A non-GET request is checked
 * against `write_guard.ts` first, so a path the UI may not write to is refused before anything
 * leaves the device. Non-2xx answers become a typed `LandingBoardError`; a 401 on a signed
 * request also tells the owner the session is gone, so the shell can drop to login.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Built by `index.ts`; used by
 *   `session.ts`, `push.ts` and the store's feeds. The only caller of `Browser.fetch`.
 *
 * Key invariants:
 *   - Request and response shapes come from the generated types via `call_types.ts`; there is
 *     no untyped call, so a route the document does not carry cannot be reached.
 *   - `postAudio` and `postImage` are the only bodies that are not JSON: raw bytes under their own
 *     format.
 *   - The bytes hashed are the bytes sent; the target signed is the path and query as sent.
 *   - A request with no session is sent unsigned (login and the other public routes).
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 6 and section 8.
 *   - src/landing_board/client/write_guard.ts for the one write path.
 */
import type { InitArgs, Method, OperationOf, PathsWith, QueryOf, ResponseOf } from '../call_types';
import type { AudioRecorder, Browser, Bytes, RecordedClip } from './browser';
import { clientError, CLIENT_CODES, LandingBoardError } from './errors';
import type { ErrorBody } from './errors';
import type { SessionHolder } from './keys';
import { signWith } from './keys';
import { EMPTY_BODY_SHA256, newNonce, requestString, sha256Hex, unixSeconds } from './signing';
import { assertMethodAllowed } from './write_guard';

export interface HttpDeps {
  browser: Browser;
  holder: SessionHolder;
  // Called when a signed request is answered 401: the session is over.
  onUnauthorized: () => void;
}

interface Outgoing {
  method: string;
  target: string;
  body: Bytes | null;
  // The body's media type; JSON for every route but the audio one.
  contentType: string;
}

const JSON_TYPE = 'application/json';
const AUDIO_PATH = '/v1/chat/audio';
// The image intake route (ADR-0047): the raw image as the body, deposited as Nectar labelled C2.
const IMAGE_PATH = '/v1/nectar/images';
type AudioOp = OperationOf<typeof AUDIO_PATH, 'post'>;
// The audio route's own query (`intent`, `duration_s`, `language`), read off the document.
export type AudioIntent = QueryOf<AudioOp>['intent'];
// A clip shorter than this is not worth a request; the document asks for a length above zero.
const MIN_CLIP_SECONDS = 0.01;

// The image formats the Entrance takes are read off the operation, as `AudioMediaType` is, so the
// client can never offer a format the document does not name.
export type ImageMediaType = keyof OperationOf<
  '/v1/nectar/images',
  'post'
>['requestBody']['content'] &
  string;

export interface SharedImage {
  bytes: Bytes;
  mediaType: ImageMediaType;
}

export interface HttpClient {
  call<M extends Method, P extends PathsWith<M>>(
    method: M,
    path: P,
    ...init: InitArgs<OperationOf<P, M>>
  ): Promise<ResponseOf<OperationOf<P, M>>>;
  // Sends one recorded clip as the raw body of `POST /v1/chat/audio`: the only non-JSON body. The
  // bytes hashed for the signature are the bytes sent, and `Content-Type` is the clip's own format.
  postAudio(clip: RecordedClip, intent: AudioIntent): Promise<ResponseOf<AudioOp>>;
  // Sends one shared image as the raw body of `POST /v1/nectar/images` (needs `entrance:submit`).
  postImage(image: SharedImage): Promise<void>;
  // The platform's recorder, carried here because this is the client's one door for audio; null on
  // a device that cannot record.
  readonly recorder: AudioRecorder | null;
}

// Fills `{name}` placeholders from the call's params, percent-encoding each value.
function fillPath(template: string, params: Record<string, string> | undefined): string {
  return template.replace(/\{([^}]+)\}/g, (_, name: string) =>
    encodeURIComponent(params?.[name] ?? ''),
  );
}

function withQuery(path: string, query: Record<string, unknown> | undefined): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined) search.set(name, String(value));
  }
  const text = search.toString();
  return text ? `${path}?${text}` : path;
}

async function errorFrom(response: Response): Promise<LandingBoardError> {
  // Some refusals (a rate limit, a body too large) are bare and carry no ErrorBody.
  const parsed: unknown = await response.json().catch(() => null);
  const body = parsed as Partial<ErrorBody> | null;
  if (body && typeof body.error === 'string' && typeof body.detail === 'string') {
    return new LandingBoardError(response.status, body as ErrorBody);
  }
  return new LandingBoardError(response.status, {
    error: 'hivemind.entrance.http_refused',
    detail: `The Hive answered ${response.status}.`,
  });
}

async function signedHeaders(deps: HttpDeps, outgoing: Outgoing): Promise<Record<string, string>> {
  const session = deps.holder.get();
  const headers: Record<string, string> = {};
  if (outgoing.body) headers['Content-Type'] = outgoing.contentType;
  if (!session) return headers;
  const { browser } = deps;
  const timestamp = unixSeconds(browser.nowMs());
  const nonce = newNonce(browser.randomBytes);
  const bodySha256 = outgoing.body
    ? await sha256Hex(browser.subtle, outgoing.body)
    : EMPTY_BODY_SHA256;
  const signed = requestString({
    method: outgoing.method,
    target: outgoing.target,
    timestamp,
    nonce,
    bodySha256,
  });
  headers.Authorization = `Bearer ${session.token}`;
  headers['X-Hive-Timestamp'] = String(timestamp);
  headers['X-Hive-Nonce'] = nonce;
  headers['X-Hive-Signature'] = await signWith(browser.subtle, session.privateKey, signed);
  return headers;
}

async function send(deps: HttpDeps, outgoing: Outgoing): Promise<unknown> {
  // The write list is checked on the bare path, before any signing or network work.
  assertMethodAllowed(outgoing.method, outgoing.target.split('?')[0] ?? outgoing.target);
  const headers = await signedHeaders(deps, outgoing);
  const hadSession = deps.holder.get() !== null;
  let response: Response;
  try {
    response = await deps.browser.fetch(deps.browser.origin + outgoing.target, {
      method: outgoing.method,
      headers,
      ...(outgoing.body ? { body: outgoing.body as BodyInit } : {}),
    });
  } catch {
    throw clientError(CLIENT_CODES.network, 'The request could not be sent.');
  }
  if (!response.ok) {
    if (response.status === 401 && hadSession) deps.onUnauthorized();
    throw await errorFrom(response);
  }
  return response.status === 204 ? undefined : response.json();
}

function encodeBody(body: unknown): Bytes | null {
  return body === undefined ? null : new TextEncoder().encode(JSON.stringify(body));
}

export function createHttp(deps: HttpDeps): HttpClient {
  return {
    recorder: deps.browser.recorder ?? null,
    async postAudio(clip, intent) {
      // A compressed clip must state its length; a WAV's header is read instead and ignores it.
      const duration = Math.max(MIN_CLIP_SECONDS, Math.round(clip.durationS * 100) / 100);
      const target = withQuery(AUDIO_PATH, { intent, duration_s: duration });
      const result = await send(deps, {
        method: 'POST',
        target,
        body: clip.bytes,
        contentType: clip.mediaType,
      });
      return result as ResponseOf<AudioOp>;
    },
    async postImage(image) {
      await send(deps, {
        method: 'POST',
        target: IMAGE_PATH,
        body: image.bytes,
        contentType: image.mediaType,
      });
    },
    async call(method, path, ...init) {
      const options = (init[0] ?? {}) as {
        params?: Record<string, string>;
        query?: Record<string, unknown>;
        body?: unknown;
      };
      const target = withQuery(fillPath(path as string, options.params), options.query);
      const result = await send(deps, {
        method: method.toUpperCase(),
        target,
        body: encodeBody(options.body),
        contentType: JSON_TYPE,
      });
      // The cast is the one place the wire meets the generated type: the document is the contract.
      return result as never;
    },
  };
}
