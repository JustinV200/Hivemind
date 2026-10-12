/**
 * The Observation Hive's composition root.
 *
 * The Observation Hive is the Hive's live read-only dashboard: the Queen's and every bee's
 * thoughts, Cell diagrams, the fleet list and more (README.md, phase 12), with one write path,
 * the chat into the Queen's inbox. This file only wires: it builds the `Browser`, the store and
 * the Landing Board client, restores a stored session, and shows the login screen or the shell
 * according to the session slice. It holds no behaviour of its own.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web). Mounted by src/main.tsx. Tests pass a fake `Browser` so the whole
 *   app runs without a network.
 *
 * Key invariants:
 *   - The store and client are built once per mounted app, never per render.
 *   - The platform is chosen before this mounts (src/main.tsx); this file never asks which it is.
 *   - A client-side 401 ends the session through the store, so the shell and its sockets go away.
 *
 * See Also:
 *   - src/store/ for the state, src/shell/ for the screens after login, src/views/login/.
 *   - src/store/registry.ts for how a view's slice joins the table below.
 *   - tests/app.test.tsx for the app run against a fake browser.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { createLandingBoard, realBrowser } from './landing_board/client';
import type { Browser } from './landing_board/client';
import { Shell } from './shell';
import { PhoneHost } from './share';
import {
  createHiveStore,
  HiveProvider,
  SESSION_ENDED_NOTICE,
  SHELL_SLICES,
  useHive,
  useSlice,
} from './store';
import type { AppSlices, Hive } from './store';
import { LoginScreen } from './views/login/login';
import { attendantSlice } from './views/attendant';
import { cappingSlice } from './views/capping';
import { cellSlice } from './views/cell';
import { chatSlice } from './views/chat';
import { costSlice } from './views/cost';
import { devicesSlice } from './views/devices';
import { enrolSlice } from './views/enrol';
import { fleetSlice } from './views/fleet';
import { forageSlice } from './views/forage';
import { honeySlice } from './views/honey';
import { thoughtsSlice } from './views/thoughts/slice';

// Every slice the app holds: the shell's three, then one per view that keeps live state. The type
// is the registry the views merge into, so a view slice left out of this table fails to compile.
export const APP_SLICES: AppSlices = {
  ...SHELL_SLICES,
  thoughts: thoughtsSlice,
  fleet: fleetSlice,
  cell: cellSlice,
  forage: forageSlice,
  attendant: attendantSlice,
  chat: chatSlice,
  cost: costSlice,
  capping: cappingSlice,
  honey: honeySlice,
  devices: devicesSlice,
  enrol: enrolSlice,
};

function buildHive(browser: Browser): Hive {
  const store = createHiveStore(APP_SLICES);
  const client = createLandingBoard({
    browser,
    onSessionLost: () =>
      store.dispatch({ slice: 'session', event: { type: 'ended', notice: SESSION_ENDED_NOTICE } }),
  });
  return { store, client };
}

// Picks the screen from the session slice and restores a stored session once at mount.
function Gate(): ReactElement {
  const { client, store } = useHive();
  const { status } = useSlice('session');

  useEffect(() => {
    void client.session.restore().then((session) => {
      store.dispatch({ slice: 'session', event: { type: 'restored', session } });
    });
  }, [client, store]);

  if (status === 'restoring') {
    return (
      <main className="login" id="main">
        <p>Opening the Observation Hive</p>
      </main>
    );
  }
  return status === 'active' ? <Shell /> : <LoginScreen />;
}

export default function App(props: { browser?: Browser }): ReactElement {
  const [{ hive, browser }] = useState(() => {
    const chosen = props.browser ?? realBrowser();
    return { hive: buildHive(chosen), browser: chosen };
  });
  return (
    <HiveProvider hive={hive}>
      <Gate />
      {/* Draws only what the platform offers (the Android shell's share sheet and invite box). */}
      <PhoneHost browser={browser} />
    </HiveProvider>
  );
}
