/**
 * The phone host: the few screens and background duties that exist only inside the Android shell.
 *
 * ADR-0047 keeps one build, so nothing here is a separate app; each part is offered by an optional
 * `Browser` member and renders nothing when the member is absent. A web page, which has none of
 * them, gets an empty fragment. On the phone the host draws the invite box (until an Entrance
 * address is known), the confirmation card for a shared item (once logged in), a card offering push
 * notices, and keeps the "a question is waiting" notification up from the push stream.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Rendered by `src/app.tsx` beside the gate, inside the
 *   Hive provider, with the same `Browser` the client was built from.
 *
 * Key invariants:
 *   - Nothing here makes a request except through the client and the chat's own writes.
 *   - A shared item is held (in memory) while logged out; the card appears after login, and a
 *     single line tells the human it is waiting.
 *   - The loopback-only screens are untouched: the phone's session is never on the loopback listener.
 *
 * See Also:
 *   - src/share/README.md for the files, and docs/adr/0047 for the rules.
 */
import type { ReactElement } from 'react';
import type { Browser, SharedFile, ShareIntake, WaitingNotifier } from '../landing_board/client';
import { useSlice } from '../store';
import './share.css';
import { InvitePaste } from './setup/invite_paste';
import { PushCard } from './setup/push_card';
import { ShareCard } from './intake/share_card';
import { useSharedItems } from './intake/use_shared_items';
import { useWaitingNotice } from './waiting/use_waiting';

interface Measuring {
  measure?: (clip: SharedFile) => Promise<number>;
}

function Shares(props: { intake: ShareIntake; origin: string } & Measuring): ReactElement | null {
  const { status } = useSlice('session');
  const items = useSharedItems(props.intake);
  if (!items.current) return null;
  if (status !== 'active') {
    return (
      <p className="phone-card" role="status">
        Something you shared is waiting. Log in to review it.
      </p>
    );
  }
  return (
    <ShareCard
      item={items.current}
      origin={props.origin}
      onFinish={items.finish}
      {...(props.measure ? { measure: props.measure } : {})}
    />
  );
}

function Waiting(props: { notifier: WaitingNotifier }): null {
  useWaitingNotice(props.notifier);
  return null;
}

export function PhoneHost(props: { browser: Browser } & Measuring): ReactElement | null {
  const { browser, measure } = props;
  const { status } = useSlice('session');
  const { inviteLinks, shareIntake, waitingNotifier } = browser;
  // Only a platform that owns a distributor registration offers `unsubscribeWebPush`.
  const pushHere = browser.unsubscribeWebPush !== undefined && status === 'active';
  if (!inviteLinks && !shareIntake && !waitingNotifier && !pushHere) return null;
  return (
    <div className="phone-host">
      {inviteLinks && !inviteLinks.configured ? <InvitePaste links={inviteLinks} /> : null}
      {shareIntake ? (
        <Shares intake={shareIntake} origin={browser.origin} {...(measure ? { measure } : {})} />
      ) : null}
      {pushHere ? <PushCard store={browser.store} /> : null}
      {waitingNotifier ? <Waiting notifier={waitingNotifier} /> : null}
    </div>
  );
}
