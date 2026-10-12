/**
 * The top bar: the Hive's name, the connection word, the device and listener, and log out.
 *
 * The connection dot says whether the live streams are connected (`live`, `reconnecting` or
 * `offline`), and its word is announced politely when it changes. The device name comes from the
 * device's own record and falls back to its id until that loads. The listener (`loopback` on the
 * Hive Stand, `remote` anywhere else) is shown because it decides which screens exist.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `shell.tsx` inside the sticky head.
 *
 * Key invariants:
 *   - Log out ends the session on the Entrance and deletes the key locally, then the session
 *     slice drops the app to the login screen.
 *   - Nothing here reads the network; it shows slices.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4.4.
 */
import type { ReactElement } from 'react';
import { StatusDot } from '../components';
import { connectionWord, useHive, useSlice } from '../store';

export function TopBar(): ReactElement {
  const { client, store } = useHive();
  const session = useSlice('session');
  const connection = useSlice('connection');

  const logOut = async (): Promise<void> => {
    await client.session.logout();
    store.dispatch({ slice: 'session', event: { type: 'left' } });
  };

  return (
    <div className="topbar">
      <span className="topbar-brand">HiveMind</span>
      <span aria-live="polite">
        <StatusDot word={connectionWord(connection)} />
      </span>
      <span className="topbar-device">
        <span>{session.deviceName ?? session.deviceId}</span>
        <span className="topbar-listener">{session.listener}</span>
      </span>
      <button type="button" className="button" onClick={() => void logOut()}>
        Log out
      </button>
    </div>
  );
}
