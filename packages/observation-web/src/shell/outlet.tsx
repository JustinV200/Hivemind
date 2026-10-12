/**
 * The outlet: draws the view for the current route.
 *
 * One view at a time fills the main area. This is the only place that maps a route name to a
 * component, so adding a view means adding one case here and one directory under `src/views/`.
 * An unknown route, or a loopback-only screen on a remote listener, draws "not found".
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `shell.tsx` inside `<main>`.
 *
 * Key invariants:
 *   - The route (and so the loopback gate) comes from `useRoute`, never recomputed here.
 *   - Not found is plain: it does not say whether the screen exists elsewhere.
 *
 * See Also:
 *   - src/router/routes.ts for the table.
 *   - docs/observation/design.md section 5.
 */
import type { ReactElement } from 'react';
import { ViewTitle } from '../components';
import { useRoute } from '../router';
import { AttendantScreen } from '../views/attendant';
import { CappingScreen } from '../views/capping';
import { CellPageView } from '../views/cell';
import { ChatScreen } from '../views/chat';
import { CostScreen } from '../views/cost';
import { DevicesView } from '../views/devices/devices';
import { EnrolView } from '../views/enrol/enrol';
import { FleetView } from '../views/fleet/fleet';
import { ForageScreen } from '../views/forage';
import { HoneyScreen } from '../views/honey';
import { ThoughtsView } from '../views/thoughts/thoughts_view';

export function Outlet(): ReactElement {
  const route = useRoute();
  switch (route.name) {
    case 'fleet':
      return <FleetView />;
    case 'cell':
      return <CellPageView id={route.id} />;
    case 'forage':
      return <ForageScreen />;
    case 'thoughts':
      return <ThoughtsView bee={route.bee} />;
    case 'attendant':
      return <AttendantScreen principal={route.principal} />;
    case 'chat':
      return <ChatScreen />;
    case 'honey':
      return <HoneyScreen path={route.path} />;
    case 'cost':
      return <CostScreen />;
    case 'capping':
      return <CappingScreen />;
    case 'devices':
      return <DevicesView />;
    case 'enrol':
      return <EnrolView />;
    case 'not_found':
      return <ViewTitle title="Not found" />;
  }
}
