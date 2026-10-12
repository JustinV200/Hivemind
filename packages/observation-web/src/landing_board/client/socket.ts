/**
 * The one `WebSocket` wrapper: first-frame authentication, parsed frames, reconnect by close code.
 *
 * A browser cannot set headers on a WebSocket, so a stream authenticates with its first frame, a
 * `SocketHello` signed over `hive-ws-v1` (path and query exactly as requested, a timestamp, a
 * nonce) and sent within the document's deadline. After that the wrapper hands each parsed frame
 * to the caller and applies ADR-0046's fixed reconnect policy by close code: `4409` resumes from
 * the last cursor at once; `4410` and `4411` end the session; `4401` and `4403` are refusals that
 * are shown and not retried; `1001`, `1008` and network loss back off 1, 2, 4, 8, then 30 seconds,
 * and the backoff resets once a connection is accepted.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Built by `index.ts`; used by the
 *   store's feeds, one stream per slice. The only caller of `Browser.openSocket`.
 *
 * Key invariants:
 *   - The hello is the first and only thing sent until the Entrance accepts it.
 *   - A connection counts as accepted at its first frame, or when the hello deadline passes with
 *     the socket still open (the Entrance closes a refused hello within that deadline).
 *   - `close()` ends the stream for good: no timer survives it and no reconnect follows.
 *
 * See Also:
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for the reconnect policy.
 *   - docs/entrance/openapi.json, `x-hive-streams` and `x-hive-socket-close`.
 */
import type { Browser, SocketLike } from './browser';
import type { SessionHolder } from './keys';
import { signWith } from './keys';
import { newNonce, socketString, unixSeconds } from './signing';

// x-hive-streams: first_frame.deadline_s.
export const HELLO_DEADLINE_MS = 5000;
// ADR-0046: 1, 2, 4, 8 seconds, then 30 at most.
export const BACKOFF_MS: readonly number[] = [1000, 2000, 4000, 8000, 30000];

export type StreamState = 'connecting' | 'live' | 'reconnecting' | 'closed' | 'refused';
export type EndReason = 'session_ended' | 'reduced' | 'refused';

export interface StreamEnd {
  reason: EndReason;
  code: number;
}

export interface StreamOptions<F> {
  // The stream's path, e.g. `/v1/cells/stream`.
  path: string;
  onFrame: (frame: F) => void;
  onState: (state: StreamState) => void;
  onEnd: (end: StreamEnd) => void;
  // For a stream that can resume: read a frame's cursor and build the query that resumes after it.
  resume?: { cursorOf: (frame: F) => string | null; query: (cursor: string) => string };
}

export interface StreamHandle {
  close(): void;
}

export interface SocketDeps {
  browser: Browser;
  holder: SessionHolder;
}

type Policy = 'resume' | 'session_ended' | 'reduced' | 'refused' | 'finished' | 'backoff';

// The close code table, as one pure function so each code's policy is easy to read and test.
export function policyFor(code: number): Policy {
  switch (code) {
    case 4409:
      return 'resume';
    case 4410:
      return 'session_ended';
    case 4411:
      return 'reduced';
    case 4401:
    case 4403:
      return 'refused';
    case 1000:
      return 'finished';
    default:
      return 'backoff';
  }
}

export function backoffDelay(attempt: number): number {
  return BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 30000;
}

function webSocketUrl(origin: string, target: string): string {
  return origin.replace(/^http/, 'ws') + target;
}

// One stream's connection state: the socket, the timers, the cursor and the backoff attempt.
class Stream<F> {
  private socket: SocketLike | null = null;
  private timer: number | null = null;
  private cursor: string | null = null;
  private attempt = 0;
  private stopped = false;
  private accepted = false;

  constructor(
    private readonly deps: SocketDeps,
    private readonly options: StreamOptions<F>,
  ) {}

  start(): void {
    this.options.onState('connecting');
    this.connect();
  }

  // Ends the stream for good: no timer survives it and no reconnect follows.
  close = (): void => {
    this.stopped = true;
    this.clearTimer();
    const open = this.socket;
    this.socket = null;
    open?.close(1000);
    this.options.onState('closed');
  };

  private clearTimer(): void {
    if (this.timer !== null) this.deps.browser.clearTimer(this.timer);
    this.timer = null;
  }

  private markAccepted = (): void => {
    if (this.accepted) return;
    this.accepted = true;
    this.attempt = 0;
    // The deadline timer has done its job once the connection is known good.
    this.clearTimer();
    this.options.onState('live');
  };

  private async sendHello(target: string, ws: SocketLike): Promise<void> {
    const { browser, holder } = this.deps;
    const session = holder.get();
    if (!session) return;
    const timestamp = unixSeconds(browser.nowMs());
    const nonce = newNonce(browser.randomBytes);
    const signature = await signWith(
      browser.subtle,
      session.privateKey,
      socketString({ target, timestamp, nonce }),
    );
    // The hello is the first frame the Entrance reads, and nothing else is sent before it.
    if (!this.stopped && this.socket === ws) {
      ws.send(JSON.stringify({ token: session.token, timestamp, nonce, signature }));
    }
  }

  private connect = (): void => {
    const { browser } = this.deps;
    const { path, resume } = this.options;
    const target = path + (this.cursor !== null && resume ? resume.query(this.cursor) : '');
    const ws = browser.openSocket(webSocketUrl(browser.origin, target));
    this.socket = ws;
    this.accepted = false;
    ws.onOpen = () => {
      void this.sendHello(target, ws);
      // Surviving the hello deadline open means the Entrance accepted the hello.
      this.timer = browser.setTimer(this.markAccepted, HELLO_DEADLINE_MS);
    };
    ws.onMessage = (data) => {
      this.markAccepted();
      this.receive(data);
    };
    ws.onClose = (code) => {
      if (this.socket === ws) this.onClosed(code);
    };
  };

  private receive(data: string): void {
    let frame: unknown;
    try {
      frame = JSON.parse(data);
    } catch {
      return;
    }
    if (typeof frame !== 'object' || frame === null) return;
    // The cast is the wire meeting the generated frame type chosen by the caller.
    const typed = frame as F;
    const next = this.options.resume?.cursorOf(typed);
    if (next !== undefined && next !== null) this.cursor = next;
    this.options.onFrame(typed);
  }

  private reconnectIn(ms: number): void {
    this.options.onState('reconnecting');
    this.timer = this.deps.browser.setTimer(this.connect, ms);
  }

  private onClosed(code: number): void {
    this.clearTimer();
    this.socket = null;
    if (this.stopped) return;
    const policy = policyFor(code);
    if (policy === 'resume') return this.reconnectIn(0);
    if (policy === 'backoff') {
      const delay = backoffDelay(this.attempt);
      this.attempt += 1;
      return this.reconnectIn(delay);
    }
    this.stopped = true;
    if (policy === 'finished') return this.options.onState('closed');
    this.options.onState(policy === 'refused' ? 'refused' : 'closed');
    this.options.onEnd({ reason: policy, code });
  }
}

export function openStream<F>(deps: SocketDeps, options: StreamOptions<F>): StreamHandle {
  const stream = new Stream(deps, options);
  stream.start();
  return { close: stream.close };
}
