/**
 * The shell: top bar, step-up banner, summary strip, nav and the main area.
 *
 * Everything after login sits inside the shell. The head (bar, banner, strip) is sticky so the
 * counters never scroll away or disappear while the human navigates; the nav is a bottom bar on a
 * phone and a rail beside the content from 720px. The shell also owns the live feeds: it starts
 * them when it mounts (a session is active) and stops them when it unmounts (logout or the
 * session ending), so there is no stream without a session and none left behind.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `src/app.tsx` when the session slice is
 *   `active`; draws views through the outlet.
 *
 * Key invariants:
 *   - The skip link focuses `<main>` in place; it must not change the hash, which is the router.
 *   - Feeds start once per mount and their cleanup closes every socket.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 7.
 *   - src/store/feed.ts for the feeds.
 */
import { useEffect } from 'react';
import type { MouseEvent, ReactElement } from 'react';
import { startFeeds, useHive } from '../store';
import { Nav } from './nav';
import { Outlet } from './outlet';
import { StepUpBanner } from './step_up_banner';
import { SummaryStrip } from './summary_strip';
import { TopBar } from './top_bar';

// An in-page link to #main would change the hash and so the route; focus it directly instead.
function skipToMain(event: MouseEvent): void {
  event.preventDefault();
  document.getElementById('main')?.focus();
}

export function Shell(): ReactElement {
  const { client, store } = useHive();

  useEffect(() => startFeeds({ client, store, nowMs: () => Date.now() }), [client, store]);

  return (
    <div className="shell">
      <a className="skip-link" href="#main" onClick={skipToMain}>
        Skip to content
      </a>
      <header className="shell-head">
        <TopBar />
        <StepUpBanner />
        <SummaryStrip />
      </header>
      <Nav />
      <main id="main" className="shell-main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
