/**
 * The real `Browser`: binds the interface to this page's globals.
 *
 * This is the only place in the front end that names `fetch`, `WebSocket`, `crypto.subtle`,
 * `indexedDB` and the timer functions, and it does so in a few lines each. It lives under
 * `landing_board/client/` because that folder is the one allowed to reach the network (ADR-0046).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Called by `src/main.tsx` (or
 *   `src/app.tsx` when none is passed) to build the `Browser` handed to `createLandingBoard`; the
 *   Android shell calls it with overrides.
 *
 * Key invariants:
 *   - The origin is the page's own, because the Entrance serves this build itself and a browser's
 *     `Origin` must be the Entrance's own (landing-board.md section 8.2).
 *   - Globals are read when the function runs, not at import, so importing this file in a test
 *     has no effect.
 *
 * See Also:
 *   - src/landing_board/client/browser.ts for the interface and tests/support/fake_browser.ts for
 *     the fake used by tests.
 */
import type { Browser, SocketLike } from '../browser';
import { indexedDbStore } from './indexed_db';
import { assertWithPasskey, createWithPasskey } from './passkey';
import { realRecorder } from './recorder';
import { subscribeWebPush } from './web_push';

// Adapts a WebSocket to SocketLike so the stream wrapper never sees DOM events.
function wrapSocket(url: string): SocketLike {
  const socket = new WebSocket(url);
  const wrapped: SocketLike = {
    onOpen: null,
    onMessage: null,
    onClose: null,
    send: (data) => socket.send(data),
    close: (code) => socket.close(code),
  };
  socket.onopen = () => wrapped.onOpen?.();
  socket.onmessage = (event) => {
    // The Landing Board speaks JSON text frames only; anything else is ignored.
    if (typeof event.data === 'string') wrapped.onMessage?.(event.data);
  };
  socket.onclose = (event) => wrapped.onClose?.(event.code);
  return wrapped;
}

// `overrides` lets another binding (the Android shell) replace members while keeping the rest.
export function realBrowser(overrides: Partial<Browser> = {}): Browser {
  const recorder = realRecorder();
  return {
    origin: window.location.origin,
    fetch: (url, init) => window.fetch(url, init),
    openSocket: wrapSocket,
    subtle: window.crypto.subtle,
    randomBytes: (length) => window.crypto.getRandomValues(new Uint8Array(length)),
    nowMs: () => Date.now(),
    setTimer: (callback, ms) => window.setTimeout(callback, ms),
    clearTimer: (id) => window.clearTimeout(id),
    store: overrides.store ?? indexedDbStore(window.indexedDB),
    assertPasskey: assertWithPasskey,
    createPasskey: createWithPasskey,
    subscribeWebPush,
    // A device that cannot record simply carries no recorder, so push-to-talk is hidden.
    ...(recorder ? { recorder } : {}),
    ...overrides,
  };
}
