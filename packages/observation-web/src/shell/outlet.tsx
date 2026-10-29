/**
 * The outlet: draws the view for the current route, each inside its own boundary.
 *
 * One view at a time fills the main area. This is the only place that maps a route name to a
 * component, so adding a view means adding one case here and one directory under `src/views/`.
 * An unknown route, or a loopback-only screen on a remote listener, draws "not found". Every
 * view is wrapped in a `ViewBoundary` keyed by the route, so a view that throws on a malformed
 * answer shows the error notice in its place while the shell stays, and moving to another route
 * draws afresh.
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
import { ViewBoundary } from '../components';
import { hrefFor, useRoute } from '../router';
import type { Route } from '../router';
import { AttendantScreen } from '../views/attendant';
import { CappingScreen } from '../views/capping';
import { CellPageView } from '../views/cell';
import { ChatScreen } from '../views/chat';
import { CostScreen } from '../views/cost';
import { DevicesScreen } from '../views/devices';
import { EnrolScreen } from '../views/enrol';
import { FleetView } from '../views/fleet/fleet';
import { ForageScreen } from '../views/forage';
import { HelpScreen } from '../views/help';
import { HoneyScreen } from '../views/honey';
import { ThoughtsView } from '../views/thoughts/thoughts_view';
import { NotFound } from './not_found';

function viewFor(route: Route): ReactElement {
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
      return <HoneyScreen path={route.path} item={route.item} />;
    case 'cost':
      return <CostScreen />;
    case 'capping':
      return <CappingScreen />;
    case 'devices':
      return <DevicesScreen />;
    case 'enrol':
      return <EnrolScreen />;
    case 'help':
      return <HelpScreen page={route.page} section={route.section} />;
    case 'not_found':
      return <NotFound />;
  }
}

export function Outlet(): ReactElement {
  const route = useRoute();
  // Keyed by the route's address, so a boundary that caught a failure is replaced on navigation.
  return <ViewBoundary key={hrefFor(route)}>{viewFor(route)}</ViewBoundary>;
}
