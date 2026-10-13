/**
 * Tests for the Cost view: the seed, the tables, sorting, the live fold, the re-read and refusals.
 *
 * The screen is mounted under a provider on the fake browser (the slice is added to the store here,
 * so these tests hold before and after the app lists it). A test answers `GET /v1/cost`, drives the
 * trail socket by hand and looks at what was drawn: the totals strip, the goal, slot and provider
 * tables, the unattributed row kept last and explained, the sort by header, a completed call folded
 * at once, the one throttled re-read after a burst, and that no read happens without a frame.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/cost/ end to end below the shell.
 *
 * Key invariants:
 *   - The view makes one read, `GET /v1/cost`, and opens one socket, `/v1/trail/stream`; a further
 *     read happens only after a folded `llm.call` frame, a second after the first of a burst.
 *
 * See Also:
 *   - tests/views/cost/slice.test.ts for the reducer rules.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { flush } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import {
  callEvent,
  costFrom,
  costReads,
  line,
  mountCost,
  sampleLines,
  trailFrame,
} from './support';

const pause = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

async function send(fake: FakeBrowser, frame: unknown): Promise<void> {
  await act(async () => {
    fake.sockets[0]!.receive(frame);
    await flush();
  });
}

const table = (name: RegExp): HTMLElement => screen.getByRole('table', { name });
const bodyRows = (name: RegExp): HTMLElement[] => within(table(name)).getAllByRole('row').slice(1);
const firstCells = (name: RegExp): string[] =>
  bodyRows(name).map((row) => within(row).getAllByRole('cell')[0]!.textContent ?? '');
const figure = (term: string): string =>
  screen.getByText(term).closest('.cost-figure')?.textContent ?? '';

describe('seed', () => {
  it('reads the cost once and opens only the trail stream', async () => {
    const { fake } = await mountCost();
    expect(await screen.findByRole('table', { name: /Spend per goal/ })).toBeTruthy();
    expect(fake.requests.map((r) => `${r.method} ${r.target}`)).toEqual(['GET /v1/cost']);
    expect(fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/trail/stream',
    ]);
  });

  it('shows the totals strip: total spend, hosted spend and the local saving', async () => {
    await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    expect(figure('Total spend')).toContain('$0.0470');
    expect(figure('Hosted spend')).toContain('$0.0470');
    expect(figure('Hosted spend')).toContain('4 of 7 calls cost money');
    expect(figure('If local, saves')).toContain('$0.0470');
  });

  it('formats a dollar or more with two decimals and thousands separators', async () => {
    await mountCost({
      cost: costFrom([line({ goal_id: 'goal_1', cost_usd: 1234.5, input_tokens: 1234567 })]),
    });
    await screen.findByRole('table', { name: /Spend per goal/ });
    expect(figure('Total spend')).toContain('$1,234.50');
    expect(within(bodyRows(/Spend per goal/)[0]!).getByText('1,234,567 / 200')).toBeTruthy();
  });
});

describe('the goal table', () => {
  it('lists goals costliest first with the unattributed row last and explained once', async () => {
    await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    expect(firstCells(/Spend per goal/)).toEqual(['goal_1', 'goal_2', 'Unattributed']);
    expect(screen.getAllByText(/Unattributed is spend whose call named no goal/)).toHaveLength(1);
  });

  it('splits each goal by slot and by provider, with tokens, calls and dollars', async () => {
    await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    const row = bodyRows(/Spend per goal/)[0]!;
    const cells = within(row).getAllByRole('cell');
    expect(cells[1]?.textContent).toBe('$0.0300');
    expect(cells[2]?.textContent).toBe('5');
    expect(cells[3]?.textContent).toBe('13,000 / 3,600');
    const texts = (cell: HTMLElement): string[] =>
      within(cell)
        .getAllByRole('listitem')
        .map((li) => li.textContent ?? '');
    expect(texts(cells[4]!)).toEqual(['queen $0.0300', 'worker $0.0000']);
    expect(texts(cells[5]!)).toEqual(['cloud $0.0300', 'local $0.0000']);
  });

  it('sorts by a header, flips on a second click, and keeps unattributed last', async () => {
    await mountCost();
    const goals = await screen.findByRole('table', { name: /Spend per goal/ });
    const spend = within(goals).getByRole('columnheader', { name: /Spend/ });
    expect(spend.getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(within(spend).getByRole('button'));
    expect(spend.getAttribute('aria-sort')).toBe('ascending');
    expect(firstCells(/Spend per goal/)).toEqual(['goal_2', 'goal_1', 'Unattributed']);
    const goal = within(goals).getByRole('columnheader', { name: /Goal/ });
    fireEvent.click(within(goal).getByRole('button'));
    fireEvent.click(within(goal).getByRole('button'));
    expect(goal.getAttribute('aria-sort')).toBe('descending');
    expect(spend.getAttribute('aria-sort')).toBe('none');
    expect(firstCells(/Spend per goal/)).toEqual(['goal_2', 'goal_1', 'Unattributed']);
    // The by-slot column is a list of figures, so it offers no sort.
    expect(
      within(goals).getByRole('columnheader', { name: 'By slot' }).hasAttribute('aria-sort'),
    ).toBe(false);
  });

  it('labels every table cell so a row can stand as a card on a phone', async () => {
    await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    for (const name of [/Spend per goal/, /Spend per slot/, /Spend per provider/]) {
      const cells = within(table(name)).getAllByRole('cell');
      expect(cells.length).toBeGreaterThan(0);
      expect(cells.every((cell) => cell.getAttribute('data-label'))).toBe(true);
    }
  });
});

describe('the slot and provider tables', () => {
  it('show each slot with its local saving and a gauge of hosted calls', async () => {
    await mountCost();
    await screen.findByRole('table', { name: /Spend per slot/ });
    // Default sort: the biggest saving first.
    expect(firstCells(/Spend per slot/)).toEqual(['queen', 'worker']);
    const worker = bodyRows(/Spend per slot/)[1]!;
    const cells = within(worker).getAllByRole('cell');
    expect(cells[1]?.textContent).toBe('$0.0120');
    expect(cells[3]?.textContent).toBe('$0.0120');
    const gauge = within(worker).getByRole('meter', { name: 'Hosted share of worker calls' });
    expect(gauge.getAttribute('aria-valuenow')).toBe('1');
    expect(gauge.getAttribute('aria-valuemax')).toBe('4');
    expect(cells[4]?.textContent).toContain('1 / 4 (25%)');
  });

  it('sorts the slots by a header', async () => {
    await mountCost();
    const slots = await screen.findByRole('table', { name: /Spend per slot/ });
    fireEvent.click(within(slots).getByRole('button', { name: /Slot/ }));
    expect(firstCells(/Spend per slot/)).toEqual(['queen', 'worker']);
    fireEvent.click(within(slots).getByRole('button', { name: /Slot/ }));
    expect(firstCells(/Spend per slot/)).toEqual(['worker', 'queen']);
  });

  it('show each provider with calls, tokens and spend, costliest first', async () => {
    await mountCost();
    await screen.findByRole('table', { name: /Spend per provider/ });
    expect(firstCells(/Spend per provider/)).toEqual(['cloud', 'local']);
    const local = within(bodyRows(/Spend per provider/)[1]!).getAllByRole('cell');
    expect(local.map((c) => c.textContent)).toEqual(['local', '3', '12,000 / 3,400', '$0.0000']);
  });
});

describe('states', () => {
  it('says no spend has been recorded when the trail holds no call', async () => {
    await mountCost({ cost: costFrom([]) });
    expect(await screen.findByText(/No spend has been recorded/)).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('shows the error table sentence for a 403 and no tables', async () => {
    await mountCost({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(screen.getByText('hivemind.entrance.capability_denied')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('live', () => {
  it('folds a completed call into the tables at once, with no read yet', async () => {
    const { fake } = await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    await send(
      fake,
      trailFrame(
        callEvent({
          payload: { goal_id: 'goal_2' },
          slot: 'worker',
          provider: 'cloud',
          usage: { cached_tokens: 0, cost_usd: 0.1, input_tokens: 40, output_tokens: 8 },
        }),
      ),
    );
    expect(firstCells(/Spend per goal/)).toEqual(['goal_2', 'goal_1', 'Unattributed']);
    expect(figure('Total spend')).toContain('$0.1470');
    expect(costReads(fake)).toBe(1);
  });

  it('puts a call that names no goal under the unattributed row', async () => {
    const { fake } = await mountCost({ cost: costFrom(sampleLines().slice(0, 3)) });
    await screen.findByRole('table', { name: /Spend per goal/ });
    expect(screen.queryByText(/Unattributed is spend/)).toBeNull();
    await send(fake, trailFrame(callEvent()));
    expect(firstCells(/Spend per goal/)).toEqual(['goal_1', 'goal_2', 'Unattributed']);
    expect(screen.getAllByText(/Unattributed is spend/)).toHaveLength(1);
  });

  it('reads no more without a frame, and none for frames that carry no usage', async () => {
    const { fake } = await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    await send(fake, trailFrame(callEvent({ id: 'ev_a', usage: null })));
    await send(fake, trailFrame(callEvent({ id: 'ev_b', kind: 'task.assigned', family: 'task' })));
    // Longer than the one second window, so a read that was going to happen would have.
    await pause(1250);
    expect(costReads(fake)).toBe(1);
  });

  it('reads once, a second after the first call of a burst, however many follow', async () => {
    const { fake } = await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    fake.respond('GET /v1/cost', 200, costFrom([line({ goal_id: 'goal_9', cost_usd: 7 })]));
    await send(fake, trailFrame(callEvent({ id: 'ev_1' })));
    await send(fake, trailFrame(callEvent({ id: 'ev_2' })));
    await send(fake, trailFrame(callEvent({ id: 'ev_3' })));
    // Not before the window closes.
    await pause(500);
    expect(costReads(fake)).toBe(1);
    expect(await screen.findByText('goal_9', {}, { timeout: 2500 })).toBeTruthy();
    expect(costReads(fake)).toBe(2);
    // The read replaced the figures: the folded calls are not counted on top of it.
    expect(figure('Total spend')).toContain('$7.00');
    await pause(1250);
    expect(costReads(fake)).toBe(2);
  });

  it('opens a new window for a call that arrives after the read', async () => {
    const { fake } = await mountCost();
    await screen.findByRole('table', { name: /Spend per goal/ });
    await send(fake, trailFrame(callEvent({ id: 'ev_1' })));
    await waitFor(() => expect(costReads(fake)).toBe(2), { timeout: 2500 });
    await send(fake, trailFrame(callEvent({ id: 'ev_2' })));
    await waitFor(() => expect(costReads(fake)).toBe(3), { timeout: 2500 });
  });
});
