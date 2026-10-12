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
 *   - The bytes hashed are the bytes sent; the target signed is the path and query as sent.
 *   - A request with no session is sent unsigned (login and the other public routes).
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 6 and section 8.
 *   - src/landing_board/client/write_guard.ts for the one write path.
 */
import type { InitArgs, Method, OperationOf, PathsWith, ResponseOf } from '../call_types';
import type { Browser, Bytes } from './browser';
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
}

export interface HttpClient {
  call<M extends Method, P extends PathsWith<M>>(
    method: M,
    path: P,
    ...init: InitArgs<OperationOf<P, M>>
  ): Promise<ResponseOf<OperationOf<P, M>>>;
  // For a route the generated document does not carry yet; the caller names the shape it expects.
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
  if (outgoing.body) headers['Content-Type'] = 'application/json';
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
      });
      // The cast is the one place the wire meets the generated type: the document is the contract.
      return result as never;
    },
  };
}
