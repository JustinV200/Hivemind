/**
 * The top bar: the mark and the Hive's name, the connection word, the theme, the device, log out.
 *
 * The bar is the hive's chrome: propolis dark in the light theme, the surface itself in the dark
 * one. Left, the brand mark and the name, then a faint comb across the empty middle from the rail
 * width. Then the connection dot, which says whether the live
 * streams are connected (`live`, `connecting`, `reconnecting` or `offline`) and whose word is
 * announced politely when it changes. Then the theme toggle. On a rail (720 px and up) the device
 * name (from the device's own record, its id until that loads), the listener in words (`on the
 * Hive Stand` for the loopback one, `remote` anywhere else: it decides which screens exist, which
 * its `title` says) and the log out button
 * follow; on a phone those two move into the nav's More panel so the bar stays one 48 px row.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/head/`. Rendered by `shell.tsx` inside the sticky head.
 *
 * Key invariants:
 *   - Log out ends the session on the Entrance and deletes the key locally, then the session
 *     slice drops the app to the login screen; `useLogOut` is the one place that does it.
 *   - Nothing here reads the network; it shows slices.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4.4.
 */
import type { ReactElement } from 'react';
import { HexPattern, Mark, RAIL_MIN, StatusDot, useMinWidth } from '../../components';
import { connectionWord, useHive, useSlice } from '../../store';
import { ThemeToggle } from '../theme';

export function useLogOut(): () => void {
  const { client, store } = useHive();
  return () => {
    void client.session.logout().then(() => {
      store.dispatch({ slice: 'session', event: { type: 'left' } });
    });
  };
}

// The listener in words: the loopback one is the Hive Stand's own door, the other is remote.
const LISTENER_WORDS = { loopback: 'on the Hive Stand', remote: 'remote' } as const;
const LISTENER_MEANING =
  'How this device reached the Hive. Only a device on the Hive Stand itself can enrol others.';

// The device's name and the listener it came in through, drawn in the bar or the More panel.
export function DeviceLine(): ReactElement {
  const session = useSlice('session');
  return (
    <span className="topbar-device">
      <span>{session.deviceName ?? session.deviceId}</span>
      <span className="topbar-listener" title={LISTENER_MEANING}>
        {session.listener ? LISTENER_WORDS[session.listener] : null}
      </span>
    </span>
  );
}

export function TopBar(): ReactElement {
  const connection = useSlice('connection');
  const wide = useMinWidth(RAIL_MIN);
  const logOut = useLogOut();

  return (
    <div className="topbar">
      <span className="topbar-brand">
        <Mark />
        HiveMind
      </span>
      {/* The bar's empty middle carries a faint comb from the rail width; a phone has no middle. */}
      <span className="topbar-comb">
        <HexPattern tone="chrome" />
      </span>
      <span aria-live="polite" className="topbar-connection">
        <StatusDot word={connectionWord(connection)} />
      </span>
      <ThemeToggle />
      {wide ? (
        <>
          <DeviceLine />
          <button type="button" className="button" onClick={logOut}>
            Log out
          </button>
        </>
      ) : null}
    </div>
  );
}
