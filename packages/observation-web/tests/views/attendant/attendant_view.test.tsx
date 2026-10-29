/**
 * Tests for the Attendant view: the graph, the lists, the links, live frames and refusals.
 *
 * The screen is rendered directly over a store that holds the app's slices plus the Attendant's
 * (the app table does not hold it until the orchestrator registers it), on the real client and the
 * fake browser. A test answers the view's read, opens the one task socket by hand and sends frames.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/attendant/ through its screen component.
 *
 * Key invariants:
 *   - The screen opens exactly one socket (`/v1/tasks/stream`) and makes one read at mount; any
 *     further read is a reaction to a frame for its principal.
 *   - Every request the screen makes is a GET (it is read-only).
 *   - The re-read tests use real time (the delay is one second), so they are the slow ones.
 *
 * See Also:
 *   - tests/views/attendant/slice.test.ts for the reducer rules and layout.test.ts for geometry.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HiveProvider } from '../../../src/store';
import { AttendantScreen } from '../../../src/views/attendant';
import { flush } from '../../support/fake_browser';
import type { OpenedHive } from './support';
import { attendantView, diamond, graphFrame, inboxEntry, openHive, taskNode } from './support';

type Answer = Parameters<typeof openHive>[0];

// Mounts the screen, waits for its task socket and opens it, as the other views' tests do.
async function mount(
  answer?: Answer,
  principal = 'queen',
): Promise<OpenedHive & { view: ReturnType<typeof render> }> {
  const opened = await openHive({ ...answer, principal });
  const view = render(
    <HiveProvider hive={opened.hive}>
      <AttendantScreen principal={principal} />
    </HiveProvider>,
  );
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(1));
  await act(async () => {
    opened.fake.sockets[0]!.open();
    await flush();
  });
  return { ...opened, view };
}

const node = (id: string) => document.querySelector(`[data-task="${id}"]`) as SVGElement;

describe('the heading', () => {
  it('names the Queen and links to her thoughts', async () => {
    await mount();
    await screen.findByText('No tasks to show.', { exact: false });
    expect(screen.getByRole('heading', { name: 'Attendant' })).toBeTruthy();
    expect(screen.getByText('queen')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Thoughts of queen' }).getAttribute('href')).toBe(
      '#/thoughts/queen',
    );
    expect(screen.queryByRole('link', { name: /Cell page/ })).toBeNull();
  });

  it('names a Warden and links to its Cell page, taken from its own placed task', async () => {
    await mount(
      {
        body: attendantView({
          principal: 'warden_1',
          nodes: [taskNode({ cell_id: 'cell_7', warden_id: 'warden_1' })],
        }),
      },
      'warden_1',
    );
    const link = await screen.findByRole('link', { name: 'Cell page of cell_7' });
    expect(link.getAttribute('href')).toBe('#/cells/cell_7');
    expect(document.querySelector('.view-subject')?.textContent).toBe('warden_1');
  });

  it('shows the title and principal at once, before the read has answered', async () => {
    const opened = await openHive();
    render(
      <HiveProvider hive={opened.hive}>
        <AttendantScreen principal="queen" />
      </HiveProvider>,
    );
    expect(screen.getByRole('heading', { name: 'Attendant' })).toBeTruthy();
    expect(screen.getByText('Reading the Attendant view')).toBeTruthy();
    await screen.findByText('Nothing is waiting in this inbox.');
  });
});

describe('the task graph', () => {
  it('draws a titled node per task with status, placement and Alarm count', async () => {
    await mount({ body: diamond() });
    const svg = await screen.findByRole('img', {
      name: /Task graph of queen: 4 tasks, 2 dependencies/,
    });
    expect(node('task_2').querySelector('title')?.textContent).toBe(
      'Task task_2, running, role drone, on cell_1, Warden warden_1, 2 open Alarms',
    );
    expect(node('task_2').getAttribute('aria-label')).toContain('2 open Alarms');
    // The count is said with its word, never a bare number in a coloured pill.
    expect(node('task_2').querySelector('[data-alarms="2"]')?.textContent).toBe('2 Alarms');
    expect(node('task_1').querySelector('[data-alarms]')).toBeNull();
    expect(node('task_3').textContent).toContain('pending');
    expect(node('task_3').textContent).toContain('not placed yet');
    expect(node('task_4').textContent).toContain('blocked');
    expect(svg.querySelectorAll('path[marker-end]')).toHaveLength(2);
  });

  it('cuts a real id to its kind and ULID tail, so it never runs under the Alarm words', async () => {
    const id = 'task_01J9TASK000000000000000001';
    await mount({ body: attendantView({ nodes: [taskNode({ task_id: id, open_alarms: 1 })] }) });
    await screen.findByRole('img', { name: /Task graph/ });
    expect(node(id).querySelector('.ag-id')?.textContent).toBe('task_…00001');
    expect(node(id).querySelector('[data-alarms="1"]')?.textContent).toBe('1 Alarm');
  });

  it('keeps colours to tokens', async () => {
    await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    const painted = Array.from(document.querySelectorAll('svg [fill], svg [stroke]')).flatMap(
      (el) => [el.getAttribute('fill'), el.getAttribute('stroke')],
    );
    const literal = painted.filter(
      (v) => v && v !== 'none' && !v.startsWith('var(') && !v.startsWith('url('),
    );
    expect(literal).toEqual([]);
  });

  it('is drawn the same on every render', async () => {
    const first = await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    const before = document.querySelector('svg.ag-svg')!.outerHTML;
    first.view.rerender(
      <HiveProvider hive={first.hive}>
        <AttendantScreen principal="queen" />
      </HiveProvider>,
    );
    expect(document.querySelector('svg.ag-svg')!.outerHTML).toBe(before);
  });

  it('links a node to its bee, else to its Cell, and leaves an unplaced one bare', async () => {
    await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    expect(node('task_1').tagName.toLowerCase()).toBe('a');
    expect(node('task_1').getAttribute('href')).toBe('#/thoughts/warden_1');
    // task_4 names no bee but is placed: its Cell page.
    expect(node('task_4').getAttribute('href')).toBe('#/cells/cell_1');
    // task_3 is not placed: nothing to open.
    expect(node('task_3').tagName.toLowerCase()).toBe('g');
    expect(node('task_3').getAttribute('href')).toBeNull();
  });

  it('has a hidden table that says each task and what it waits on', async () => {
    await mount({ body: diamond() });
    const table = await screen.findByRole('table', { name: 'Task graph of queen' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(4);
    const third = within(rows[2]!)
      .getAllByRole('cell')
      .map((c) => c.textContent);
    expect(third).toEqual(['pending', 'drone', 'not placed yet', 'none', '0', 'task_1, task_2']);
    expect(table.parentElement?.className).toContain('sr-only');
  });
});

describe('the lists', () => {
  const body = diamond({
    inbox: [
      inboxEntry({
        id: 'alarm_9',
        kind: 'ALARM',
        severity: 'CRITICAL',
        score: 91.25,
        task_id: 'task_2',
      }),
      inboxEntry({
        id: 'q_1',
        kind: 'QUESTION',
        score: 40,
        reasons: ['asked twice', 'blocks task_4'],
      }),
      inboxEntry({ id: 'hm_1', kind: 'HUMAN_MESSAGE', score: 12, reasons: [] }),
    ],
    recently_finished: ['task_1', 'task_0'],
    up_next: ['task_3'],
  });

  it('show the inbox in the Entrance order with rank, score, kind and reasons', async () => {
    await mount({ body });
    const inbox = within(await screen.findByRole('region', { name: /Inbox/ }));
    const items = inbox.getAllByRole('listitem').filter((li) => li.className === 'att-entry');
    expect(items).toHaveLength(3);
    expect(items[0]!.textContent).toContain('1');
    expect(items[0]!.textContent).toContain('Alarm');
    expect(items[0]!.textContent).toContain('critical');
    expect(items[0]!.textContent).toContain('score 91.25');
    expect(items[0]!.textContent).toContain('task task_2');
    expect(items[1]!.textContent).toContain('question');
    expect(within(items[1]!).getByText('blocks task_4')).toBeTruthy();
    expect(items[2]!.textContent).toContain('message from you');
    expect(inbox.queryByText(/Estimated order/)).toBeNull();
  });

  it('say when the inbox was reconstructed', async () => {
    await mount({ body: { ...body, inbox_is_reconstructed: true } });
    expect(await screen.findByText(/Estimated order, scored from saved state/)).toBeTruthy();
  });

  it('list what finished and what is next, with the graph status where it is known', async () => {
    await mount({ body });
    const done = within(await screen.findByRole('region', { name: 'Recently finished' }));
    expect(done.getAllByRole('listitem')).toHaveLength(2);
    expect(done.getByText('succeeded, on cell_1')).toBeTruthy();
    // task_0 is not in the graph: only its id.
    expect(done.getByTitle('task_0')).toBeTruthy();
    const next = within(screen.getByRole('region', { name: 'Up next' }));
    expect(next.getByText('pending, not placed yet')).toBeTruthy();
  });
});

describe('the empty and refused states', () => {
  it('say so in each part when there is nothing', async () => {
    await mount({ body: attendantView() });
    expect(await screen.findByText(/No tasks to show/)).toBeTruthy();
    expect(screen.getByText('Nothing is waiting in this inbox.')).toBeTruthy();
    expect(screen.getByText('Nothing has finished recently.')).toBeTruthy();
    expect(screen.getByText('Nothing is waiting to start.')).toBeTruthy();
    expect(document.querySelector('svg.ag-svg')).toBeNull();
  });

  it('shows the error table sentence for an unknown principal (404) and no view', async () => {
    await mount(
      { status: 404, body: { error: 'hivemind.entrance.not_found', detail: 'x' } },
      'warden_404',
    );
    expect(
      await screen.findByText('That does not exist, or this listener does not serve it.'),
    ).toBeTruthy();
    expect(document.querySelector('[data-code="hivemind.entrance.not_found"]')).toBeTruthy();
    expect(screen.queryByText('Task graph')).toBeNull();
  });

  it('shows the error table sentence for a 403 and no view', async () => {
    await mount({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(screen.queryByText('Inbox, in the Attendant’s order')).toBeNull();
    expect(screen.queryByRole('region', { name: /Inbox/ })).toBeNull();
  });
});

describe('live frames', () => {
  it("moves a node's status and placement at once, and leaves the Alarm count", async () => {
    const opened = await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    act(() => {
      opened.fake.sockets[0]!.receive(
        graphFrame('queen', { id: 'task_2', status: 'FAILED', cell_id: 'cell_5', warden_id: null }),
      );
    });
    expect(node('task_2').textContent).toContain('failed');
    expect(node('task_2').textContent).toContain('on cell_5');
    expect(node('task_2').getAttribute('href')).toBe('#/cells/cell_5');
    expect(node('task_2').querySelector('[data-alarms="2"]')).toBeTruthy();
  });

  it("ignores a frame for another principal's task", async () => {
    const opened = await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    act(() => {
      opened.fake.sockets[0]!.receive(graphFrame('warden_9', { id: 'task_2', status: 'FAILED' }));
    });
    expect(node('task_2').textContent).toContain('running');
  });

  it('opens one task socket, signs nothing else, and closes it when the reader leaves', async () => {
    const opened = await mount({ body: diamond() });
    expect(opened.fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/tasks/stream',
    ]);
    opened.view.unmount();
    expect(opened.fake.sockets[0]!.closedWith).toBe(1000);
    expect(opened.fake.requests.every((r) => r.method === 'GET')).toBe(true);
  });
});

describe('the re-read', () => {
  it('makes no read without a frame, and none for a frame about another principal', async () => {
    const opened = await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    act(() => {
      opened.fake.sockets[0]!.receive(graphFrame('warden_9', { id: 'task_1' }));
    });
    // Longer than the one second window, so a read that was going to happen would have.
    await new Promise((resolve) => setTimeout(resolve, 1250));
    expect(opened.reads()).toBe(1);
  });

  it('reads once, a second after the first frame, however many follow', async () => {
    const opened = await mount({ body: diamond() });
    await screen.findByRole('img', { name: /Task graph/ });
    opened.fake.respond(
      'GET /v1/attendant/queen',
      200,
      diamond({
        inbox: [inboxEntry({ id: 'alarm_new', kind: 'ALARM', score: 77 })],
        nodes: [...diamond().nodes, taskNode({ task_id: 'task_5', status: 'PENDING' })],
      }),
    );
    act(() => {
      const tasks = opened.fake.sockets[0]!;
      tasks.receive(graphFrame('queen', { id: 'task_1' }));
      tasks.receive(graphFrame('queen', { id: 'task_2', status: 'FAILED' }));
      tasks.receive(graphFrame('queen', { id: 'task_5' }));
    });
    // The nodes moved at once; the inbox waits for the read.
    expect(screen.queryByText('score', { exact: false })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(opened.reads()).toBe(1);
    expect(await screen.findByText('score', { exact: false }, { timeout: 2500 })).toBeTruthy();
    expect(opened.reads()).toBe(2);
    // A task created since the seed arrives with the read.
    expect(node('task_5')).toBeTruthy();
    // The three frames cost one read: nothing else follows.
    await new Promise((resolve) => setTimeout(resolve, 1250));
    expect(opened.reads()).toBe(2);
  });
});
