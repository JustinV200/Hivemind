/**
 * `ViewBoundary`: catches a view that throws while drawing, so the shell survives it.
 *
 * One malformed field in one answer must not blank the whole app. Each view is drawn inside this
 * boundary; when it throws, the boundary shows the error notice in the view's place (the code
 * `hivemind.client.view_failed`, a sentence and what to do) and the top bar, strip and nav stay.
 * The boundary is keyed by the route in the outlet, so moving to another view, or back, draws
 * afresh rather than staying stuck on the notice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by `src/shell/outlet.tsx` around every
 *   view. A class, because React gives `getDerivedStateFromError` to classes only.
 *
 * Key invariants:
 *   - The thrown value never reaches the page: the notice shows the fixed code and words.
 *   - The failure is logged once to the console for the operator; nothing is sent anywhere.
 *
 * See Also:
 *   - src/components/error_notice.tsx for the notice; src/landing_board/client/errors.ts.
 */
import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { CLIENT_CODES, clientError } from '../landing_board/client/errors';
import { ErrorNotice } from './error_notice';

interface State {
  failed: boolean;
}

export class ViewBoundary extends Component<{ children: ReactNode }, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // The operator reads the console; the page shows only the fixed words.
    console.error('Observation Hive: a view failed to draw', error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.failed) {
      return <ErrorNotice error={clientError(CLIENT_CODES.viewFailed, 'A view threw.')} />;
    }
    return this.props.children;
  }
}
