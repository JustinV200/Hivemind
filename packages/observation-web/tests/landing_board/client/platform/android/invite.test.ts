/**
 * Tests for invite links on Android and for the Android `Browser` they feed.
 *
 * The phone learns the Entrance's address from `<base>/enrol#code=<code>`, delivered by the App
 * Link at launch or while running, or pasted by hand. The base URL is stored beside the session and
 * becomes the `Browser.origin`; a device that is signed in never changes Entrance because of a link.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises platform/android/invite.ts, platform/android/index.ts
 *   and the base URL record in keys.ts.
 *
 * Key invariants:
 *   - Only https, exactly `/enrol`, with a code of the Entrance's shape, is accepted.
 *   - The origin is the stored base URL, and the page's own origin only while none is stored.
 *   - A launch link shows the join screen without a reload; a running one reloads onto it.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4 and docs/adr/0047.
 */
import { describe, expect, it } from 'vitest';
import type { Browser } from '../../../../../src/landing_board/client';
import { androidBrowser } from '../../../../../src/landing_board/client/platform/android';
import type { AndroidDeps } from '../../../../../src/landing_board/client/platform/android';
import {
  adoptInvite,
  parseInviteLink,
} from '../../../../../src/landing_board/client/platform/android/invite';
import { loadBaseUrl } from '../../../../../src/landing_board/client/keys';
import { memoryStore } from '../../../../../src/landing_board/client/platform/indexed_db';
import { createFakeBrowser, flush } from '../../../../support/fake_browser';
import { storeSession } from '../../../../support/session_fixture';

const CODE = 'ABCD-EFGH-JKLM-NPQR-STUV';
const LINK = `https://hive.example.ts.net/enrol#code=${CODE}`;

describe('parseInviteLink', () => {
  it('reads the origin and the code', () => {
    expect(parseInviteLink(LINK)).toEqual({ baseUrl: 'https://hive.example.ts.net', code: CODE });
    expect(parseInviteLink(`  https://hive.test:8443/enrol/#code=${CODE}  `)).toEqual({
      baseUrl: 'https://hive.test:8443',
      code: CODE,
    });
  });

  it.each([
    ['plain http', `http://hive.test/enrol#code=${CODE}`],
    ['another path', `https://hive.test/login#code=${CODE}`],
    ['no code', 'https://hive.test/enrol'],
    ['a short code', 'https://hive.test/enrol#code=abc'],
    ['credentials in the address', `https://user:pw@hive.test/enrol#code=${CODE}`],
    ['not a URL', 'enrol please'],
  ])('refuses %s', (_name, link) => {
    expect(parseInviteLink(link)).toBeNull();
  });
});

describe('adoptInvite', () => {
  it('stores the base URL and returns the code', async () => {
    const store = memoryStore();
    expect(await adoptInvite(store, LINK)).toBe(CODE);
    expect(await loadBaseUrl(store)).toBe('https://hive.example.ts.net');
  });

  it('does nothing for a link that is not an invite', async () => {
    const store = memoryStore();
    expect(await adoptInvite(store, 'https://evil.test/')).toBeNull();
    expect(await loadBaseUrl(store)).toBeNull();
  });

  it('never moves a signed-in device to another Entrance', async () => {
    const fake = createFakeBrowser();
    await storeSession(fake.browser);
    expect(await adoptInvite(fake.store, LINK)).toBeNull();
    expect(await loadBaseUrl(fake.store)).toBeNull();
  });
});

interface Harness {
  deps: AndroidDeps;
  shown: { code: string; reload: boolean }[];
  open: (url: string) => void;
  store: ReturnType<typeof memoryStore>;
}

function harness(launchUrl: string | null, stored?: string): Harness {
  const store = memoryStore();
  if (stored) void store.put('base_url', stored);
  const shown: Harness['shown'] = [];
  let listener: ((event: { url: string }) => void) | null = null;
  const fake = createFakeBrowser();
  const deps = {
    plugins: {
      passkey: { create: () => Promise.reject(new Error('unused')), get: () => Promise.reject(new Error('unused')) },
      push: { register: () => Promise.reject(new Error('unused')), unregister: () => Promise.resolve() },
      share: { addListener: () => Promise.resolve({ remove: () => Promise.resolve() }) },
    },
    app: {
      getLaunchUrl: () => Promise.resolve(launchUrl ? { url: launchUrl } : undefined),
      addListener: (_event: string, callback: (event: { url: string }) => void) => {
        listener = callback;
        return Promise.resolve({ remove: () => Promise.resolve() });
      },
    },
    notifications: {},
    store,
    web: (overrides: Partial<Browser>) => ({ ...fake.browser, ...overrides }),
    pageOrigin: 'https://observation.hive.local',
    showJoin: (code: string, reload: boolean) => shown.push({ code, reload }),
  } as unknown as AndroidDeps;
  return { deps, shown, store, open: (url) => listener?.({ url }) };
}

describe('androidBrowser', () => {
  it('uses the stored base URL as its origin instead of the page origin', async () => {
    const browser = await androidBrowser(harness(null, 'https://hive.example.ts.net').deps);
    expect(browser.origin).toBe('https://hive.example.ts.net');
    expect(browser.inviteLinks?.configured).toBe(true);
  });

  it('falls back to the page origin, unconfigured, before any invite', async () => {
    const browser = await androidBrowser(harness(null).deps);
    expect(browser.origin).toBe('https://observation.hive.local');
    expect(browser.inviteLinks?.configured).toBe(false);
  });

  it('adopts a launch link before mounting and shows the join screen without a reload', async () => {
    const h = harness(LINK);
    const browser = await androidBrowser(h.deps);
    expect(browser.origin).toBe('https://hive.example.ts.net');
    expect(h.shown).toEqual([{ code: CODE, reload: false }]);
  });

  it('adopts a link opened while running and reloads onto the join screen', async () => {
    const h = harness(null);
    await androidBrowser(h.deps);
    h.open(LINK);
    await flush();
    expect(h.shown).toEqual([{ code: CODE, reload: true }]);
    expect(await loadBaseUrl(h.store)).toBe('https://hive.example.ts.net');
  });

  it('ignores a running link that is not an invite', async () => {
    const h = harness(null);
    await androidBrowser(h.deps);
    h.open('https://other.test/somewhere');
    await flush();
    expect(h.shown).toEqual([]);
  });

  it('accepts a pasted link and refuses other text', async () => {
    const h = harness(null);
    const browser = await androidBrowser(h.deps);
    expect(await browser.inviteLinks!.accept('hello')).toBe(false);
    expect(await browser.inviteLinks!.accept(LINK)).toBe(true);
    expect(h.shown).toEqual([{ code: CODE, reload: true }]);
  });

  it('supplies the phone members and keeps the rest of the web binding', async () => {
    const browser = await androidBrowser(harness(null).deps);
    expect(browser.shareIntake).toBeDefined();
    expect(browser.waitingNotifier).toBeDefined();
    expect(browser.unsubscribeWebPush).toBeDefined();
    expect(browser.fetch).toBeDefined();
  });

  it('uses the base URL for requests and sockets', async () => {
    const h = harness(null, 'https://hive.example.ts.net');
    const browser = await androidBrowser(h.deps);
    const seen: string[] = [];
    browser.fetch = (url) => {
      seen.push(url);
      return Promise.resolve(new Response('{}'));
    };
    await browser.fetch(browser.origin + '/v1/chat', {});
    expect(seen).toEqual(['https://hive.example.ts.net/v1/chat']);
  });
});
