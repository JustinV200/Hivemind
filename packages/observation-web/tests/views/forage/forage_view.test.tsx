/**
 * Tests for the Forage view: the seed, the stream, the diagrams, the tables and a refusal.
 *
 * The screen is mounted under a provider on the fake browser (the slice is added to the store here,
 * so these tests hold before and after the app lists it). A test answers the three reads, drives the
 * forage socket by hand and looks at what was drawn: that the view reads exactly the seed and then
 * only reacts to a capacity frame, that a frame redraws at once, that grants nearing expiry come
 * first and are marked in words, that the map sorts by header, and that a 403 shows the error table.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/forage/ end to end below the shell.
 *
 * Key invariants:
 *   - The view makes the three seed reads and opens one socket, `/v1/forage/stream`, and no other
 *     request unless a `forage.capacity_reported` frame arrives.
 *
 * See Also:
 *   - tests/views/forage/slice.test.ts for the reducer rules.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { flush } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import {
  capacity,
  eventView,
  forageFrame,
  forageView,
  grant,
  headroom,
  inMinutes,
  mountForage,
  warden,
} from './support';

const socketOf = (fake: FakeBrowser) => fake.sockets[0]!;
const reads = (fake: FakeBrowser): string[] => fake.requests.map((r) => `${r.method} ${r.target}`);

// The shared pool's seat figures, read from the numbers table that follows the pool's cards.
const seatsFigures = (): string =>
  within(screen.getByRole('table', { name: /Shared pool, in numbers/ })).getByRole('row', {
    name: /Shared model seats/,
  }).textContent ?? '';

async function send(fake: FakeBrowser, frame: unknown): Promise<void> {
  await act(async () => {
    socketOf(fake).receive(frame);
    await flush();
  });
}

describe('seed', () => {
  it('sends the three reads together and opens only the forage stream', async () => {
    const { fake } = await mountForage();
    expect(await screen.findByRole('table', { name: /^Grants/ })).toBeTruthy();
    expect(reads(fake).sort()).toEqual(['GET /v1/forage', 'GET /v1/llm', 'GET /v1/wardens']);
    expect(fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/forage/stream',
    ]);
  });

  it('draws the pool, the hosts and the flow with accessible names and tokens only', async () => {
    await mountForage();
    const pool = await screen.findByRole('group', { name: /^Shared pool:/ });
    const hosts = screen.getByRole('group', { name: /^Capacity per host:/ });
    const flowImg = screen.getByRole('img', { name: /Forage flow/ });
    expect(flowImg.getAttribute('viewBox')).toMatch(/^0 0 480 /);
    for (const figure of [pool, hosts, flowImg]) {
      expect(figure.outerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    }
    // Every host row is a bar with its figures in its name, and the same numbers follow in a table.
    expect(within(hosts).getAllByRole('img', { name: /^Cores: / }).length).toBeGreaterThan(0);
    expect(screen.getByRole('table', { name: /Capacity per host, in numbers/ })).toBeTruthy();
  });

  it('says what would fill an empty ledger', async () => {
    await mountForage({
      forage: forageView({ capacities: [], grants: [] }),
      wardens: { wardens: [] },
    });
    expect(await screen.findByText(/No grants yet/)).toBeTruthy();
    expect(screen.getByText(/No Cell has reported capacity yet/)).toBeTruthy();
    expect(screen.getByText(/No requests since this view opened/)).toBeTruthy();
  });

  it('shows the error table sentence for a 403 and no diagrams', async () => {
    await mountForage({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(screen.getByText('hivemind.entrance.capability_denied')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });
});

describe('no read without a frame', () => {
  it('stays at the three seed reads while nothing arrives and while ordinary frames do', async () => {
    const { fake } = await mountForage();
    await screen.findByRole('table', { name: /^Grants/ });
    await act(async () => {
      fake.advance(120_000);
      await flush();
    });
    expect(fake.requests).toHaveLength(3);
    await send(
      fake,
      forageFrame(eventView('forage.granted', 'grant_1'), { grant: grant({ seats: 3 }) }),
    );
    expect(fake.requests).toHaveLength(3);
  });

  it('reads the ledger again once per capacity report, as a reaction to its frame', async () => {
    const { fake } = await mountForage();
    await screen.findByRole('table', { name: /^Grants/ });
    fake.respond(
      'GET /v1/forage',
      200,
      forageView({ capacities: [capacity({ cell_id: 'cell_9' })] }),
    );
    await send(fake, forageFrame(eventView('forage.capacity_reported', 'cell_9')));
    await waitFor(() => expect(fake.requests).toHaveLength(4));
    expect(fake.requests.at(-1)?.target).toBe('/v1/forage');
    expect((await screen.findAllByText('cell_9')).length).toBeGreaterThan(0);
  });
});

describe('frames', () => {
  it('redraw a grant and the shared pool at once', async () => {
    const { fake } = await mountForage();
    await screen.findByRole('table', { name: /^Grants/ });
    // Seed: reserve 2, granted 2, free 6.
    expect(seatsFigures()).toContain('2 granted, 2 reserve of 10');
    await send(
      fake,
      forageFrame(eventView('forage.granted', 'grant_1'), {
        grant: grant({ seats: 5, tokens_spent: 700 }),
        headroom: headroom({ shared_seats: 0 }),
      }),
    );
    expect(screen.getByText('700 / 1,000')).toBeTruthy();
    expect(seatsFigures()).toContain('5 granted, 2 reserve of 7');
  });

  it('drops a revoked grant from the table', async () => {
    const { fake } = await mountForage();
    await screen.findByRole('table', { name: /^Grants/ });
    await send(fake, forageFrame(eventView('forage.revoked', 'grant_1')));
    expect(screen.queryByRole('table', { name: /^Grants/ })).toBeNull();
    expect(screen.getByText(/No grants yet/)).toBeTruthy();
  });

  it('shows a request as pending, then the Queen decision with its reason', async () => {
    const { fake } = await mountForage();
    await screen.findByRole('table', { name: /^Grants/ });
    await send(
      fake,
      forageFrame(eventView('forage.requested', 'grant_1', { request_kind: 'SPEND' }, 'ev_1')),
    );
    const table = screen.getByRole('table', { name: /Forage requests/ });
    expect(within(table).getByText('pending')).toBeTruthy();
    expect(within(table).getByText('SPEND')).toBeTruthy();
    await send(
      fake,
      forageFrame(eventView('forage.denied', 'grant_1', { reason: 'over_daily_cap' }, 'ev_2')),
    );
    expect(within(table).getByText('denied')).toBeTruthy();
    expect(within(table).getByText('over_daily_cap')).toBeTruthy();
    expect(within(table).queryByText('pending')).toBeNull();
  });
});

describe('grants nearing expiry', () => {
  it('are listed first and marked in words as well as the warn token', async () => {
    await mountForage({
      forage: forageView({
        grants: [
          grant({ id: 'g_late', expires_at: inMinutes(90) }),
          grant({ id: 'g_soon', expires_at: inMinutes(4) }),
        ],
      }),
    });
    const table = await screen.findByRole('table', { name: /^Grants/ });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]?.textContent).toContain('g_soon');
    expect(rows[0]?.className).toContain('forage-near');
    expect(rows[0]?.textContent).toContain('expiring soon');
    expect(rows[1]?.textContent).toContain('g_late');
    expect(rows[1]?.className).not.toContain('forage-near');
  });
});

describe('the Forage map', () => {
  const modelOrder = (): string[] =>
    within(screen.getByRole('table', { name: /Forage map/ }))
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[1]!.textContent ?? '');

  it('sorts by a header, flips on a second click and says so with aria-sort', async () => {
    await mountForage();
    const map = await screen.findByRole('table', { name: /Forage map/ });
    const header = within(map).getByRole('columnheader', { name: /Model/ });
    expect(header.getAttribute('aria-sort')).toBe('none');
    expect(
      within(map)
        .getByRole('columnheader', { name: /Binding/ })
        .getAttribute('aria-sort'),
    ).toBe('ascending');
    fireEvent.click(within(header).getByRole('button'));
    expect(header.getAttribute('aria-sort')).toBe('ascending');
    expect(modelOrder()).toEqual(['big-1', 'small-1']);
    fireEvent.click(within(header).getByRole('button'));
    expect(header.getAttribute('aria-sort')).toBe('descending');
    expect(modelOrder()).toEqual(['small-1', 'big-1']);
  });

  it('shows each source with its provider kind and health as a word', async () => {
    await mountForage();
    const map = await screen.findByRole('table', { name: /Forage map/ });
    expect(within(map).getByText('HEALTHY')).toBeTruthy();
    expect(within(map).getByText('DEGRADED')).toBeTruthy();
    expect(within(map).getByText('openai_compat')).toBeTruthy();
  });
});

describe('the flow unit and the phone layout', () => {
  it('changes the whole flow at once when the unit changes', async () => {
    await mountForage();
    const spend = await screen.findByRole('button', { name: 'Spend (USD)' });
    expect(screen.getByRole('button', { name: 'Model seats' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    fireEvent.click(spend);
    expect(spend.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Model seats' }).getAttribute('aria-pressed')).toBe(
      'false',
    );
    expect(
      screen.getAllByText(/Grant grant_1 for task_1: \$5\.00, used \$1\.00/).length,
    ).toBeGreaterThan(0);
  });

  it('labels every table cell so a row can stand as a card', async () => {
    await mountForage({ wardens: { wardens: [warden()] } });
    const table = await screen.findByRole('table', { name: /^Grants/ });
    const cells = within(table).getAllByRole('cell');
    expect(cells.length).toBeGreaterThan(0);
    expect(cells.every((cell) => cell.getAttribute('data-label'))).toBe(true);
    expect(screen.getByRole('table', { name: /Wardens and the grants/ })).toBeTruthy();
  });
});
