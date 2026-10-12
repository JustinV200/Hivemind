/**
 * The Landing Board client's face: build the one client the app talks to the Hive through.
 *
 * `createLandingBoard` wires the pieces in this folder around one injected `Browser`: the session
 * holder (the live key and token), the signing HTTP wrapper, the session, the stream opener and
 * push registration. The app builds exactly one, and everything that reaches the network does so
 * through it; no other module touches `fetch` or `WebSocket` (ADR-0046).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Built by `src/app.tsx`; consumed by
 *   the store's feeds, the login screen and the shell.
 *
 * Key invariants:
 *   - `onSessionLost` runs when a signed request is answered 401, after local state is cleared.
 *   - The pieces share one `SessionHolder`, so a login is visible to requests and sockets alike.
 *
 * See Also:
 *   - docs/adr/0046-observation-hive-dashboard-stack.md.
 *   - src/app.tsx, the composition root.
 */
import type { Browser } from './browser';
import { createHttp } from './http';
import type { HttpClient } from './http';
import { createHolder } from './keys';
import { createPush } from './push';
import type { PushClient } from './push';
import { createSessionClient } from './session';
import type { SessionClient } from './session';
import { openStream } from './socket';
import type { StreamHandle, StreamOptions } from './socket';

export { describeError, LandingBoardError } from './errors';
export { realBrowser } from './platform/real_browser';
export type { DescribedError } from './errors';
export type { DeviceDescription, Enrolled, OpenedSession, SessionClient } from './session';
export type { StreamEnd, StreamHandle, StreamOptions, StreamState } from './socket';
export type { Browser } from './browser';
export type { HttpClient } from './http';

export interface LandingBoard {
  session: SessionClient;
  http: HttpClient;
  push: PushClient;
  openStream<F>(options: StreamOptions<F>): StreamHandle;
}

export interface LandingBoardOptions {
  browser: Browser;
  onSessionLost: () => void;
}

export function createLandingBoard(options: LandingBoardOptions): LandingBoard {
  const { browser } = options;
  const holder = createHolder();
  const http = createHttp({
    browser,
    holder,
    onUnauthorized: () => {
      // Clear the key and token first, then tell the shell, so nothing signs with a dead session.
      void session.forget().then(options.onSessionLost);
    },
  });
  const session = createSessionClient({ browser, http, holder });
  const push = createPush(browser, http);
  return {
    session,
    http,
    push,
    openStream: (streamOptions) => openStream({ browser, holder }, streamOptions),
  };
}
