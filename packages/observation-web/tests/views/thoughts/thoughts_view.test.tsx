/**
 * Tests for the Thoughts view: the seed, the sections, the Queen's inbox, Follow and refusals.
 *
 * The view is mounted through the real `App` on the fake browser, so the route, the store, the
 * client, the stream hook and the components are exercised together. A test signs in with a stored
 * session, answers the reads it needs, and drives the episodes socket by hand.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/thoughts/ through src/app.tsx.
 *
 * Key invariants:
 *   - No request is made that the test did not route; an unrouted read answers 404.
 *   - The view makes the seed read(s) and opens one extra socket (the episodes stream) and nothing
 *     more: it never polls.
 *
 * See Also:
 *   - tests/views/thoughts/slice.test.ts for the reducer rules.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import App from '../../../src/app';
import { createFakeBrowser, flush } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import {
  attendantView,
  decisionView,
  episodeFrame,
  episodeView,
  inboxEntry,
  SEED,
  thoughtsPage,
  trailFrame,
} from '../../support/frames';
import { storeSession } from '../../support/session_fixture';

afterEach(() => {
  window.location.hash = '';
});

interface Options {
  hash?: string;
  page?: unknown;
  attendant?: unknown;
  thoughtsStatus?: number;
  thoughtsBody?: unknown;
}

// Opens the app on a thoughts route; resolves once the view's own stream socket exists.
async function openThoughts(options: Options = {}): Promise<FakeBrowser> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  fake.respond('GET /v1/devices/me', 200, { name: 'Pixel 9' });
  fake.respond('GET /v1/summary', 200, SEED);
  fake.respond(
    'GET /v1/thoughts',
    options.thoughtsStatus ?? 200,
    options.thoughtsBody ?? options.page,
  );
  fake.respond('GET /v1/attendant/queen', 200, options.attendant ?? attendantView());
  window.location.hash = options.hash ?? '#/thoughts/queen';
  render(<App browser={fake.browser} />);
  // The four the shell opens, the episodes stream, and the Queen's two decision streams.
  const opening = (options.hash ?? '#/thoughts/queen') === '#/thoughts/queen' ? 7 : 5;
  await waitFor(() => expect(fake.sockets).toHaveLength(opening));
  await act(async () => {
    fake.sockets.forEach((socket) => socket.open());
    await flush();
  });
  return fake;
}

// The view's effects run before the shell's, so its streams come first.
const socketAt = (fake: FakeBrowser, path: string) =>
  fake.sockets.find((socket) => socket.url.endsWith(path))!;
const episodesSocket = (fake: FakeBrowser) => socketAt(fake, '/v1/episodes/stream');

const newest = episodeView({
  id: 'ep_new',
  at: '2026-01-01T00:00:10Z',
  decision: 'Rebind the Cell',
  action: 'REBIND',
  trigger_summary: 'A lease expired',
  prompt_ref: 'prompt_abc',
  reasoning_summary: 'The lease ran out, so rebinding is safest.',
});
const oldest = episodeView({ id: 'ep_old', at: '2026-01-01T00:00:01Z', decision: 'Wait' });

describe('seed', () => {
  it('reads the newest 50 for the bee and renders each episode', async () => {
    const fake = await openThoughts({ page: thoughtsPage({ episodes: [newest, oldest] }) });
    expect(await screen.findByText('Rebind the Cell')).toBeTruthy();
    const read = fake.requests.find((r) => r.target.startsWith('/v1/thoughts'))!;
    expect(read.target).toBe('/v1/thoughts?principal=queen&limit=50');
    expect(screen.getByText('A lease expired')).toBeTruthy();
    expect(screen.getByText(/rebinding is safest/)).toBeTruthy();
    expect(screen.getAllByRole('article')).toHaveLength(2);
    // Newest first.
    const [first] = screen.getAllByRole('article');
    expect(within(first!).getByText('Rebind the Cell')).toBeTruthy();
  });

  it('opens for any bee at its own route, with no inbox panel', async () => {
    const fake = await openThoughts({
      hash: '#/thoughts/worker_9',
      page: thoughtsPage({ episodes: [episodeView({ principal: 'worker_9', decision: 'Edit' })] }),
    });
    expect(await screen.findByText('Edit')).toBeTruthy();
    expect(fake.requests.some((r) => r.target.startsWith('/v1/attendant'))).toBe(false);
    expect(screen.queryByText(/inbox, in the Attendant/)).toBeNull();
  });

  it('says what would fill an empty view', async () => {
    await openThoughts({ page: thoughtsPage() });
    expect(await screen.findByText(/No thoughts yet/)).toBeTruthy();
  });

  it('marks an autopilot episode, and shows usage only for a model call', async () => {
    await openThoughts({
      page: thoughtsPage({ episodes: [episodeView({ id: 'ep_a', is_autopilot: true })] }),
    });
    const card = (await screen.findAllByRole('article'))[0]!;
    expect(within(card).getByText('Autopilot')).toBeTruthy();
    expect(within(card).queryByRole('button', { name: 'Usage' })).toBeNull();
  });

  it('marks a tainted record with its source, and a cleared one', async () => {
    await openThoughts({
      page: thoughtsPage({
        episodes: [
          episodeView({ id: 'ep_a', taint: 'tainted', taint_source: 'quarantine' }),
          episodeView({ id: 'ep_b', taint: 'cleared', taint_source: 'isolation' }),
        ],
      }),
    });
    const cards = await screen.findAllByRole('article');
    expect(within(cards[0]!).getByText('TAINTED (quarantine)')).toBeTruthy();
    expect(within(cards[1]!).getByText('CLEARED (isolation)')).toBeTruthy();
  });
});

describe('context sections', () => {
  it('are collapsed, and open one at a time in place', async () => {
    await openThoughts({
      page: thoughtsPage({
        episodes: [{ ...newest, input_tokens: 1200, output_tokens: 30, cost_usd: 0.0123 }],
      }),
    });
    const toggle = await screen.findByRole('button', { name: 'Assembled context' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('prompt_abc')).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('prompt_abc')).toBeTruthy();
    // The other section stays shut.
    expect(screen.getByRole('button', { name: 'Usage' }).getAttribute('aria-expanded')).toBe(
      'false',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Usage' }));
    expect(screen.getByText('1,200')).toBeTruthy();
    expect(screen.getByText('$0.0123')).toBeTruthy();
  });
});

describe('the Queen', () => {
  it("shows the Attendant's ordering with scores, and the reconstructed note", async () => {
    await openThoughts({
      page: thoughtsPage(),
      attendant: attendantView({
        inbox_is_reconstructed: true,
        inbox: [
          inboxEntry({ id: 'alarm_hi', score: 9.5, reasons: ['severity high'] }),
          inboxEntry({ id: 'q_1', kind: 'QUESTION', score: 3, reasons: ['waiting long'] }),
        ],
      }),
    });
    expect(await screen.findByText(/Rebuilt from durable state/)).toBeTruthy();
    expect(screen.getByText('severity high')).toBeTruthy();
    expect(screen.getByText('9.5')).toBeTruthy();
    expect(screen.getByText('question')).toBeTruthy();
  });

  it('interleaves autopilot decisions with episodes in time order, drawn apart', async () => {
    await openThoughts({
      page: thoughtsPage({
        episodes: [newest, oldest],
        decisions: [
          decisionView({ id: 'dec_mid', at: '2026-01-01T00:00:05Z', reason: 'lease_gone' }),
        ],
      }),
    });
    await screen.findByText('Rebind the Cell');
    const rows = screen
      .getAllByRole('article')
      .map((el) => el.getAttribute('aria-label') ?? 'episode');
    expect(rows).toEqual(['episode', 'Autopilot decision', 'episode']);
    expect(screen.getByText('lease_gone')).toBeTruthy();
  });
});

describe("the Queen's real principal", () => {
  const hiveId = 'hive_01QUEEN';

  it('shows her episodes, which carry the Hive id, and the frames that follow', async () => {
    const fake = await openThoughts({
      page: thoughtsPage({
        principal: hiveId,
        episodes: [episodeView({ id: 'ep_q', principal: hiveId, decision: 'Cluster' })],
      }),
    });
    expect(await screen.findByText('Cluster')).toBeTruthy();
    act(() => {
      episodesSocket(fake).receive(
        episodeFrame({ id: 'ep_live', principal: hiveId, decision: 'Live one' }),
      );
    });
    expect(screen.getByText('Live one')).toBeTruthy();
  });
});

describe("the Queen's decisions, live", () => {
  const decided = (id: string, at: string, payload: Record<string, string>) =>
    trailFrame({ id, kind: 'queen.decided', at, subject_id: 'cell_1', payload });

  it('add a row for queen.decided and alarm.handled, and ignore other kinds', async () => {
    const fake = await openThoughts({ page: thoughtsPage({ episodes: [oldest] }) });
    await screen.findByText('Wait');
    act(() => {
      socketAt(fake, '?kind=queen.decided').receive(
        decided('dec_live', '2026-01-01T00:02:00Z', { action: 'CLUSTER', reason: 'provider_down' }),
      );
      socketAt(fake, '?kind=alarm.handled').receive(
        trailFrame({
          id: 'dec_alarm',
          kind: 'alarm.handled',
          at: '2026-01-01T00:03:00Z',
          subject_id: 'alarm_9',
          payload: { action: 'RECORD' },
        }),
      );
      socketAt(fake, '?kind=alarm.handled').receive(
        trailFrame({ id: 'noise', kind: 'task.started', at: '2026-01-01T00:04:00Z' }),
      );
    });
    expect(screen.getByText('provider_down')).toBeTruthy();
    expect(screen.getAllByRole('article', { name: 'Autopilot decision' })).toHaveLength(2);
  });

  it('are opened for the Queen only', async () => {
    const fake = await openThoughts({ hash: '#/thoughts/worker_9', page: thoughtsPage() });
    expect(fake.sockets.some((s) => s.url.includes('/v1/trail/stream'))).toBe(false);
  });
});

describe('follow', () => {
  it('prepends a new episode for this bee and ignores another bee', async () => {
    const fake = await openThoughts({ page: thoughtsPage({ episodes: [oldest] }) });
    await screen.findByText('Wait');
    act(() => {
      episodesSocket(fake).receive(
        episodeFrame({ id: 'ep_b', principal: 'worker_1', decision: 'Other' }),
      );
      episodesSocket(fake).receive(
        episodeFrame({ id: 'ep_c', decision: 'Fresh', at: '2026-01-01T00:01:00Z' }),
      );
    });
    expect(screen.queryByText('Other')).toBeNull();
    const [first] = screen.getAllByRole('article');
    expect(within(first!).getByText('Fresh')).toBeTruthy();
  });

  it('off, queues behind "N new" and reveals them on a click', async () => {
    const fake = await openThoughts({ page: thoughtsPage({ episodes: [oldest] }) });
    await screen.findByText('Wait');
    const follow = screen.getByRole('button', { name: 'Follow: on' });
    expect(follow.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(follow);
    expect(follow.getAttribute('aria-pressed')).toBe('false');
    act(() => {
      episodesSocket(fake).receive(episodeFrame({ id: 'ep_n1', decision: 'First new' }));
      episodesSocket(fake).receive(episodeFrame({ id: 'ep_n2', decision: 'Second new' }));
    });
    expect(screen.queryByText('First new')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '2 new' }));
    expect(screen.getByText('First new')).toBeTruthy();
    expect(screen.getByText('Second new')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /new$/ })).toBeNull();
  });
});

describe('older', () => {
  it('loads the next page by the cursor and appends it', async () => {
    const fake = await openThoughts({
      page: thoughtsPage({ episodes: [newest], next_cursor: 'cur_1' }),
    });
    await screen.findByText('Rebind the Cell');
    fake.respond('GET /v1/thoughts', 200, thoughtsPage({ episodes: [newest, oldest] }));
    fireEvent.click(screen.getByRole('button', { name: 'Older' }));
    expect(await screen.findByText('Wait')).toBeTruthy();
    expect(fake.requests.at(-1)!.target).toBe('/v1/thoughts?principal=queen&cursor=cur_1&limit=50');
    expect(screen.getAllByRole('article')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Older' })).toBeNull();
  });
});

describe('refusal', () => {
  it('shows the error table sentence for a 403 and no list', async () => {
    await openThoughts({
      thoughtsStatus: 403,
      thoughtsBody: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(
      document.querySelector('[data-code="hivemind.entrance.capability_denied"]'),
    ).toBeTruthy();
    expect(screen.queryByText(/No thoughts yet/)).toBeNull();
  });
});

describe('the stream', () => {
  it('is opened once with the others and closed when the reader leaves', async () => {
    const fake = await openThoughts({ page: thoughtsPage() });
    expect(fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/trail/stream?kind=queen.decided',
      '/v1/trail/stream?kind=alarm.handled',
      '/v1/episodes/stream',
      '/v1/cells/stream',
      '/v1/telemetry/stream',
      '/v1/episodes/stream',
      '/v1/push/stream',
    ]);
    await act(async () => {
      window.location.hash = '#/forage';
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    for (const index of [0, 1, 2]) expect(fake.sockets[index]!.closedWith).toBe(1000);
    expect(fake.sockets[5]!.closedWith).toBeNull();
  });
});
