/**
 * The session slice: whether a human is logged in, on which listener, and why they are not.
 *
 * The shell shows the login screen unless this slice says `active`, and the router hides
 * loopback-only screens unless `listener` is `loopback`. The reducer is a pure function of the
 * slice and one event; the events come from the login screen, the boot-time restore, the feeds
 * (a stream close with code 4410 or 4411) and the HTTP wrapper (a 401).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Folded by `HiveStore`; read by the shell, the router
 *   and the login screen through `useSlice('session')`.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O; the same slice and event give the same slice.
 *   - A listener the Entrance names as anything but `loopback` is treated as `remote`, the safe
 *     reading for deciding what to show.
 *   - `ended` carries one sentence for the login screen; the Entrance never says why a session
 *     ended, so neither does this.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 5.10.
 *   - src/landing_board/client/session.ts, where the events originate.
 */
import type { OpenedSession } from '../landing_board/client';

export type Listener = 'loopback' | 'remote';
export type SessionStatus = 'restoring' | 'none' | 'active' | 'ended';

export interface SessionSlice {
  status: SessionStatus;
  deviceId: string | null;
  deviceName: string | null;
  listener: Listener | null;
  expiresAt: string | null;
  needsStepUp: boolean;
  notice: string | null;
}

export type SessionEvent =
  | { type: 'restored'; session: OpenedSession | null }
  | { type: 'opened'; session: OpenedSession }
  | { type: 'named'; name: string }
  | { type: 'stepped_up' }
  | { type: 'ended'; notice: string }
  | { type: 'left' };

export const SESSION_ENDED_NOTICE = 'Your session ended. Log in again.';

export const initialSession: SessionSlice = {
  status: 'restoring',
  deviceId: null,
  deviceName: null,
  listener: null,
  expiresAt: null,
  needsStepUp: false,
  notice: null,
};

const loggedOut: SessionSlice = { ...initialSession, status: 'none' };

function active(session: OpenedSession): SessionSlice {
  return {
    status: 'active',
    deviceId: session.deviceId,
    deviceName: null,
    listener: session.listener === 'loopback' ? 'loopback' : 'remote',
    expiresAt: session.expiresAt,
    needsStepUp: session.needsStepUp,
    notice: null,
  };
}

export function reduceSession(slice: SessionSlice, event: SessionEvent): SessionSlice {
  switch (event.type) {
    case 'restored':
      return event.session ? active(event.session) : loggedOut;
    case 'opened':
      return active(event.session);
    case 'named':
      return slice.status === 'active' ? { ...slice, deviceName: event.name } : slice;
    case 'stepped_up':
      return slice.needsStepUp ? { ...slice, needsStepUp: false } : slice;
    case 'ended':
      // Only an active session can end; a repeat (several sockets close at once) changes nothing.
      return slice.status === 'active'
        ? { ...loggedOut, status: 'ended', notice: event.notice }
        : slice;
    case 'left':
      return loggedOut;
  }
}
