/**
 * A fake `Browser` for tests: real WebCrypto, a scripted network, manual sockets and timers.
 *
 * The client's signing and reconnect logic must be tested without a network or a real clock, and
 * without a mocking library. This fake implements the `Browser` interface by hand: `fetch` answers
 * from a table of routes and records every request, `openSocket` returns a socket the test drives
 * (open, receive, close with a code), and timers fire only when the test advances time. Crypto is
 * the real WebCrypto of the test runtime, so a signature the client makes can be verified here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the client, store, shell and app tests.
 *
 * Key invariants:
 *   - Nothing here touches the network, `window` or real timers.
 *   - Requests are recorded with their exact body bytes, so a test can hash what was sent.
 *   - An unrouted request answers 404 with no body, like a path the Entrance does not serve.
 *
 * See Also:
 *   - src/landing_board/client/browser.ts for the interface this implements.
 */
import type { Browser, Bytes, SocketLike } from '../../src/landing_board/client/browser';
import { memoryStore } from '../../src/landing_board/client/platform/indexed_db';

export interface RecordedRequest {
  method: string;
  target: string;
  headers: Record<string, string>;
  body: Bytes | null;
}

export interface FakeAnswer {
  status: number;
  body?: unknown;
}

export type Responder = (request: RecordedRequest) => FakeAnswer;

export class FakeSocket implements SocketLike {
  onOpen: (() => void) | null = null;
  onMessage: ((data: string) => void) | null = null;
  onClose: ((code: number) => void) | null = null;
  readonly sent: string[] = [];
  closedWith: number | null = null;

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(code: number): void {
    this.closedWith = code;
  }

  open(): void {
    this.onOpen?.();
  }

  receive(frame: unknown): void {
    this.onMessage?.(typeof frame === 'string' ? frame : JSON.stringify(frame));
  }

  serverClose(code: number): void {
    this.onClose?.(code);
  }
}

interface Timer {
  id: number;
  at: number;
  run: () => void;
}

// A clock and its timers, moved only by `advance`, so a test controls every delay.
class FakeClock {
  private nextId = 1;
  private timers: Timer[] = [];

  constructor(private nowMs: number) {}

  now = (): number => this.nowMs;

  pending = (): number => this.timers.length;

  set = (callback: () => void, ms: number): number => {
    const id = this.nextId++;
    this.timers.push({ id, at: this.nowMs + ms, run: callback });
    return id;
  };

  clear = (id: number): void => {
    this.timers = this.timers.filter((timer) => timer.id !== id);
  };

  advance = (ms: number): void => {
    const end = this.nowMs + ms;
    for (;;) {
      const due = this.timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      this.timers = this.timers.filter((t) => t !== due);
      this.nowMs = Math.max(this.nowMs, due.at);
      due.run();
    }
    this.nowMs = end;
  };
}

export interface FakeBrowser {
  browser: Browser;
  requests: RecordedRequest[];
  sockets: FakeSocket[];
  routes: Map<string, Responder>;
  passkeyOptions: Record<string, unknown>[];
  store: Browser['store'];
  now(): number;
  // Moves time forward, running each timer that falls due in order.
  advance(ms: number): void;
  pendingTimers(): number;
  // Answers `METHOD /path` (query ignored) with a fixed status and JSON body.
  respond(key: string, status: number, body?: unknown): void;
}

// Records the request, then answers from the route table (404 with no body if unrouted).
function scriptedFetch(requests: RecordedRequest[], routes: Map<string, Responder>) {
  return (url: string, init: RequestInit): Promise<Response> => {
    const target = url.replace(/^https?:\/\/[^/]+/, '');
    const request: RecordedRequest = {
      method: String(init.method),
      target,
      headers: { ...(init.headers as Record<string, string>) },
      body: (init.body as Bytes | undefined) ?? null,
    };
    requests.push(request);
    const route = routes.get(`${request.method} ${target.split('?')[0]}`);
    const answer = route ? route(request) : { status: 404 };
    const payload = answer.body === undefined ? null : JSON.stringify(answer.body);
    const body = answer.status === 204 ? null : payload;
    return Promise.resolve(new Response(body, { status: answer.status }));
  };
}

export function createFakeBrowser(startMs = 1_767_225_600_000): FakeBrowser {
  const clock = new FakeClock(startMs);
  const requests: RecordedRequest[] = [];
  const sockets: FakeSocket[] = [];
  const routes = new Map<string, Responder>();
  const passkeyOptions: Record<string, unknown>[] = [];
  const store = memoryStore();

  const browser: Browser = {
    origin: 'https://hive.test',
    fetch: scriptedFetch(requests, routes),
    openSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    subtle: crypto.subtle,
    randomBytes: (length) => crypto.getRandomValues(new Uint8Array(length)),
    nowMs: clock.now,
    setTimer: clock.set,
    clearTimer: clock.clear,
    store,
    assertPasskey: (options) => {
      passkeyOptions.push(options);
      return Promise.resolve({ id: 'credential', type: 'public-key', response: {} });
    },
    subscribeWebPush: () =>
      Promise.resolve({ endpoint: 'https://push.test/abc', keys: { p256dh: 'P', auth: 'A' } }),
  };

  return {
    browser,
    requests,
    sockets,
    routes,
    passkeyOptions,
    store,
    now: clock.now,
    advance: clock.advance,
    pendingTimers: clock.pending,
    respond: (key, status, body) => {
      routes.set(key, () => ({ status, body }));
    },
  };
}

// Lets pending work run before the test looks: WebCrypto signs on another thread, so a few
// macrotask turns are needed on top of the promise continuations.
export async function flush(): Promise<void> {
  for (let i = 0; i < 6; i++) {
    await new Promise((resolve) => setTimeout(resolve, 3));
  }
}
