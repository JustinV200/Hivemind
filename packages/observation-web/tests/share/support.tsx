/**
 * Test harness for the phone host: a signed-in (or logged-out) client, fakes for the phone members.
 *
 * The phone host draws only what the platform offers, so each test builds a fake `Browser` that
 * carries the members it is about: a share sheet it can fire, a notifier that records its calls, an
 * invite box, a distributor flag. The store is the app's own table, and the session slice is driven
 * directly so a test can log in after an item was shared.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the tests under tests/share/.
 *
 * Key invariants:
 *   - Nothing here asserts; tests assert on the recorded requests, notifier calls and the DOM.
 *   - The fake share sheet holds an item that arrives before anyone listens, like the native one.
 *
 * See Also:
 *   - src/share/phone_host.tsx, the component under test.
 */
import { act, render } from '@testing-library/react';
import { APP_SLICES } from '../../src/app';
import { createLandingBoard } from '../../src/landing_board/client';
import type {
  Browser,
  InviteLinks,
  ShareIntake,
  SharedItem,
  WaitingNotifier,
} from '../../src/landing_board/client';
import { PhoneHost } from '../../src/share';
import { createHiveStore, HiveProvider } from '../../src/store';
import type { Hive } from '../../src/store';
import { chatSlice } from '../../src/views/chat/slice';
import { createFakeBrowser, flush } from '../support/fake_browser';
import type { FakeBrowser } from '../support/fake_browser';
import { storeSession } from '../support/session_fixture';

export class FakeIntake implements ShareIntake {
  private listener: ((item: SharedItem) => void) | null = null;
  private early: SharedItem[] = [];
  stopped = 0;

  onShared(listener: (item: SharedItem) => void): () => void {
    this.listener = listener;
    this.early.splice(0).forEach(listener);
    return () => {
      this.listener = null;
      this.stopped += 1;
    };
  }

  share(item: SharedItem): void {
    if (this.listener) this.listener(item);
    else this.early.push(item);
  }
}

export class FakeNotifier implements WaitingNotifier {
  calls: string[] = [];
  show(count: number): Promise<void> {
    this.calls.push(`show ${count}`);
    return Promise.resolve();
  }
  clear(): Promise<void> {
    this.calls.push('clear');
    return Promise.resolve();
  }
}

export interface PhoneSetup {
  signedIn?: boolean;
  intake?: FakeIntake;
  notifier?: FakeNotifier;
  invites?: InviteLinks;
  // Gives the browser a distributor registration, as the Android shell has.
  distributor?: boolean;
  // The inbox the Entrance answers with when the host reads it at start.
  inbox?: unknown;
  measure?: (clip: { bytes: Uint8Array; mediaType: string }) => Promise<number>;
}

export interface OpenedPhone {
  fake: FakeBrowser;
  hive: Hive;
  browser: Browser;
  view: ReturnType<typeof render>;
  logIn(): Promise<void>;
}

export async function openPhone(setup: PhoneSetup = {}): Promise<OpenedPhone> {
  const fake = createFakeBrowser();
  const signedIn = setup.signedIn ?? true;
  if (signedIn) await storeSession(fake.browser);
  fake.respond('GET /v1/inbox', 200, setup.inbox ?? { alarms: [], questions: [] });
  const browser: Browser = {
    ...fake.browser,
    origin: 'https://hive.example.ts.net',
    ...(setup.intake ? { shareIntake: setup.intake } : {}),
    ...(setup.notifier ? { waitingNotifier: setup.notifier } : {}),
    ...(setup.invites ? { inviteLinks: setup.invites } : {}),
    ...(setup.distributor ? { unsubscribeWebPush: () => Promise.resolve() } : {}),
  };
  const client = createLandingBoard({ browser, onSessionLost: () => {} });
  const store = createHiveStore({ ...APP_SLICES, chat: chatSlice });
  const session = await client.session.restore();
  store.dispatch({ slice: 'session', event: { type: 'restored', session } });
  const hive: Hive = { store, client };
  const view = render(
    <HiveProvider hive={hive}>
      <PhoneHost browser={browser} {...(setup.measure ? { measure: setup.measure } : {})} />
    </HiveProvider>,
  );
  await act(flush);
  return {
    fake,
    hive,
    browser,
    view,
    async logIn() {
      await storeSession(fake.browser);
      const opened = await client.session.restore();
      await act(async () => {
        store.dispatch({ slice: 'session', event: { type: 'restored', session: opened } });
        await flush();
      });
    },
  };
}
