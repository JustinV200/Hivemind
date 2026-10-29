/**
 * The shell: top bar, step-up banner, summary strip, notice bar, nav and the main area.
 *
 * Everything after login sits inside the shell. The head (bar, banner, strip, notices) is sticky
 * so the counters never scroll away or disappear while the human navigates; the nav is a bottom
 * bar on a phone and a rail beside the content from 720px. The shell also owns the live feeds: it
 * starts them when it mounts (a session is active) and stops them when it unmounts (logout or the
 * session ending), so there is no stream without a session and none left behind. The guided tour
 * (started from the Help screen) is mounted here too, since its steps move between screens.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `src/app.tsx` when the session slice is
 *   `active`; draws views through the outlet.
 *
 * Key invariants:
 *   - The skip link focuses `<main>` in place; it must not change the hash, which is the router.
 *   - Feeds start once per mount and their cleanup closes every socket.
 *   - A dropped stream is said in words (the strip's note, the title row's note), never by
 *     dimming the main area, which would put its captions and links under 4.5:1.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 7.
 *   - src/store/feed.ts for the feeds.
 */
import { useEffect, useRef } from 'react';
import type { MouseEvent, ReactElement } from 'react';
import { startFeeds, useHive } from '../store';
import { Tour } from '../views/help';
import { NoticeBar, StepUpBanner, SummaryStrip, TopBar } from './head';
import { Nav } from './nav';
import { Outlet } from './outlet';
import { useHeadHeight } from './use_head_height';

// An in-page link to #main would change the hash and so the route; focus it directly instead.
function skipToMain(event: MouseEvent): void {
  event.preventDefault();
  document.getElementById('main')?.focus();
}

export function Shell(): ReactElement {
  const { client, store } = useHive();
  const shell = useRef<HTMLDivElement>(null);
  const head = useRef<HTMLElement>(null);

  useEffect(() => startFeeds({ client, store, nowMs: () => Date.now() }), [client, store]);
  // The rail and the sticky parts of a view sit under the head, whose height the banner and the
  // notice bar change; the measured height goes to the stylesheet as `--head-h`.
  useHeadHeight(shell, head);

  return (
    <div className="shell" ref={shell}>
      <a className="skip-link" href="#main" onClick={skipToMain}>
        Skip to content
      </a>
      <header className="shell-head" ref={head}>
        <TopBar />
        <StepUpBanner />
        <SummaryStrip />
        <NoticeBar />
      </header>
      <Nav />
      <main id="main" className="shell-main" tabIndex={-1}>
        <Outlet />
      </main>
      {/* The guided tour, mounted once so it outlives the route changes its steps make. */}
      <Tour />
    </div>
  );
}
