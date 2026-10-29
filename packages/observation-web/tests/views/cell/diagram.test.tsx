/**
 * Tests for the Cell diagram: its words, its geometry and the SVG it draws.
 *
 * The model (what each node and box says) and the layout (where they go) are pure, so they are
 * tested without React; the component is then rendered to check the Warden inside or outside the
 * frame by `warden_runs`, one titled node per sub-bee, the accessible names, the Alarm and awake
 * outlines, the gauges, token-only colours and that two renders are byte-identical.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/cell/diagram/.
 *
 * Key invariants:
 *   - No test depends on pixels: geometry is asserted as numbers in the viewBox.
 *   - The wide layout is reached by stubbing `ResizeObserver` to report a wide container.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.5, 4.6 and 4.8.
 */
import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { awakeSet, buildModel, CellDiagram, layoutDiagram } from '../../../src/views/cell/diagram';
import type { Layout } from '../../../src/views/cell/diagram';
import { alarmView, beeRow, cellRecord, pageView, wardenRow } from './support';

afterEach(() => {
  vi.unstubAllGlobals();
});

const none: ReadonlySet<string> = new Set<string>();
const model = (over: Parameters<typeof pageView>[0] = {}, awake = none) =>
  buildModel(pageView(over), awake);

// A container this wide gets the wide layout.
function stubWideContainer(width: number): void {
  class StubObserver {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(): void {
      const entry = { contentRect: { width } } as ResizeObserverEntry;
      this.callback([entry], this as unknown as ResizeObserver);
    }
    disconnect(): void {}
    unobserve(): void {}
  }
  vi.stubGlobal('ResizeObserver', StubObserver);
}

function inside(inner: { x: number; y: number; w: number; h: number }, outer: Layout['frame']) {
  return (
    inner.x >= outer.x &&
    inner.y >= outer.y &&
    inner.x + inner.w <= outer.x + outer.w &&
    inner.y + inner.h <= outer.y + outer.h
  );
}

describe('the model', () => {
  it('describes the Warden, the sub-bees by role then id, and four fact boxes', () => {
    const m = model({
      bees: [
        wardenRow(),
        beeRow({ id: 'w_b', role: 'SCOUT' }),
        beeRow({ id: 'w_c', role: 'DRONE' }),
        beeRow({ id: 'w_a', role: 'DRONE' }),
      ],
    });
    expect(m.warden?.id).toBe('warden_1');
    expect(m.bees.map((b) => b.id)).toEqual(['w_a', 'w_c', 'w_b']);
    expect(m.boxes.map((b) => b.key)).toEqual(['session', 'exoskeleton', 'lease', 'hosting']);
  });

  it('maps a reported state to the fixed word and tone, and keeps an unknown one as given', () => {
    const m = model({
      bees: [
        wardenRow(),
        beeRow({ id: 'a', status: 'RUNNING' }),
        beeRow({ id: 'b', status: 'FAILED' }),
        beeRow({ id: 'c', status: 'PAUSED' }),
        beeRow({ id: 'd', status: 'ODD' }),
      ],
    });
    const word = (id: string) => m.bees.find((b) => b.id === id);
    expect(word('a')).toMatchObject({ word: 'working', tone: 'ok' });
    expect(word('b')).toMatchObject({ word: 'failed', tone: 'alarm' });
    expect(word('c')).toMatchObject({ word: 'waiting', tone: 'warn' });
    expect(word('d')).toMatchObject({ word: 'odd', tone: 'idle' });
  });

  it('says where the Warden runs on its own line, and "not tracked" when it is null', () => {
    expect(model({ warden_runs: 'hive_stand' }).warden?.line).toBe('Hive Stand');
    expect(model({ warden_runs: 'on_cell' }).warden?.line).toBe('on the Cell');
    expect(model({ warden_runs: null }).warden?.line).toBe('not tracked');
  });

  it('marks a bee whose task has an open Alarm, and a bee in an awake episode', () => {
    const m = model(
      { alarms: [alarmView({ task_id: 'task_1' })] },
      awakeSet({ worker_1: { auto: false } }),
    );
    expect(m.bees[0]).toMatchObject({ alarmed: true, awake: true });
    expect(m.bees[0]!.title).toContain('an open Alarm names its task');
  });

  it('awakeSet keeps only principals whose latest episode was not autopilot', () => {
    expect([...awakeSet({ a: { auto: false }, b: { auto: true } })]).toEqual(['a']);
  });

  it('words the Exoskeleton as attached, not attached or unknown', () => {
    const value = (v: boolean | null) =>
      model({ exoskeleton_attached: v }).boxes.find((b) => b.key === 'exoskeleton')!.value;
    expect([value(true), value(false), value(null)]).toEqual([
      'attached',
      'not attached',
      'unknown',
    ]);
  });

  it('words the lease for a Real Cell and the status for a Virtual Cell', () => {
    const real = model({ lease_age_s: 3900 }).boxes.find((b) => b.key === 'lease')!;
    expect(real).toMatchObject({ label: 'Lease', value: 'OPEN', detail: 'age 1h 05m' });
    const virtual = model({
      cell: cellRecord({
        kind: 'VIRTUAL',
        virtual_status: 'READY',
        lease_state: null,
        source: 'docker',
      }),
    }).boxes.find((b) => b.key === 'lease')!;
    expect(virtual).toMatchObject({
      label: 'Virtual Cell',
      value: 'READY',
      detail: 'backend docker',
    });
  });

  it('words the hosting and says the session is not tracked yet', () => {
    const boxes = model().boxes;
    expect(boxes.find((b) => b.key === 'hosting')).toMatchObject({
      value: 'hybrid',
      detail: 'plan revision 3',
    });
    expect(boxes.find((b) => b.key === 'hosting')!.title).toContain('worker local');
    expect(boxes.find((b) => b.key === 'session')!.value).toBe('not tracked yet');
  });
});

describe('the layout', () => {
  const many = (n: number) =>
    model({
      bees: [wardenRow(), ...Array.from({ length: n }, (_, i) => beeRow({ id: `w_${i}` }))],
    });

  it('puts the Warden inside the frame when it runs on the Cell', () => {
    const layout = layoutDiagram(model({ warden_runs: 'on_cell' }), 'wide');
    expect(layout.wardenOutside).toBe(false);
    expect(inside(layout.warden!, layout.frame)).toBe(true);
  });

  it('puts the Warden above and outside the frame on the Hive Stand, or when not tracked', () => {
    for (const runs of ['hive_stand', null] as const) {
      const layout = layoutDiagram(model({ warden_runs: runs }), 'narrow');
      expect(layout.wardenOutside).toBe(true);
      expect(layout.warden!.y + layout.warden!.h).toBeLessThanOrEqual(layout.frame.y);
      // Its sub-bees are on the Cell, so they stay inside.
      expect(layout.bees.every((b) => inside(b, layout.frame))).toBe(true);
    }
  });

  it('wraps sub-bees by what fits: two across when narrow, three when wide, never more than six', () => {
    const rowsOf = (layout: Layout) => new Set(layout.bees.map((b) => b.y)).size;
    expect(rowsOf(layoutDiagram(many(6), 'narrow'))).toBe(3);
    expect(rowsOf(layoutDiagram(many(6), 'wide'))).toBe(2);
    expect(rowsOf(layoutDiagram(many(1), 'wide'))).toBe(1);
  });

  it('keeps every node and box inside the viewBox, wide and narrow', () => {
    for (const mode of ['wide', 'narrow'] as const) {
      const layout = layoutDiagram(many(7), mode);
      const all = [
        layout.frame,
        ...(layout.warden ? [layout.warden] : []),
        ...layout.bees,
        ...layout.boxes,
      ];
      for (const r of all) {
        expect(r.x).toBeGreaterThanOrEqual(0);
        expect(r.y).toBeGreaterThanOrEqual(0);
        expect(r.x + r.w).toBeLessThanOrEqual(layout.width);
        expect(r.y + r.h).toBeLessThanOrEqual(layout.height);
      }
    }
  });

  it('puts the boxes beside the frame when wide and under it when narrow', () => {
    const wide = layoutDiagram(model(), 'wide');
    expect(wide.boxes.every((b) => b.x >= wide.frame.x + wide.frame.w)).toBe(true);
    const narrow = layoutDiagram(model(), 'narrow');
    expect(narrow.boxes.every((b) => b.y >= narrow.frame.y + narrow.frame.h)).toBe(true);
  });

  // A phone's pane is the canvas, so 12 unit text is 12 px there: the frame fills it and the
  // sub-bees and boxes wrap to what fits (one across at 304 px, two at the design's 360).
  it.each([
    [304, 1, 1],
    [360, 2, 2],
    [500, 2, 2],
  ])('sizes the narrow canvas from a %s px pane', (pane, beeCols, boxCols) => {
    const layout = layoutDiagram(many(4), 'narrow', pane);
    expect(layout.width).toBe(pane);
    expect(layout.frame.w).toBe(pane - 8);
    expect(new Set(layout.bees.map((b) => b.x)).size).toBe(beeCols);
    expect(new Set(layout.boxes.map((b) => b.x)).size).toBe(boxCols);
    for (const r of [...layout.bees, ...layout.boxes]) expect(r.x + r.w).toBeLessThanOrEqual(pane);
  });

  it('is deterministic, and joins every sub-bee and every box with a line', () => {
    const a = layoutDiagram(many(4), 'wide');
    expect(layoutDiagram(many(4), 'wide')).toEqual(a);
    expect(a.links).toHaveLength(4 + 4);
  });
});

describe('the drawing', () => {
  const draw = (over: Parameters<typeof pageView>[0] = {}, awake = none) =>
    render(<CellDiagram page={pageView(over)} awake={awake} />);

  it('is an image with a title and a label stating what it shows', () => {
    const { container } = draw();
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('role')).toBe('img');
    const label = svg.getAttribute('aria-label')!;
    expect(label).toContain('Real Cell cell_1');
    const titleId = svg.getAttribute('aria-labelledby')!;
    expect(svg.querySelector(`title#${titleId}`)?.textContent).toBe(label);
    expect(svg.getAttribute('viewBox')).toBe(
      '0 0 360 ' + svg.getAttribute('viewBox')!.split(' ')[3],
    );
  });

  it('draws one titled link per sub-bee and one for the Warden', () => {
    const { container } = draw({
      bees: [wardenRow(), beeRow({ id: 'w_1' }), beeRow({ id: 'w_2', role: 'FORAGER' })],
    });
    const nodes = [...container.querySelectorAll('[data-bee]')];
    expect(nodes.map((n) => n.getAttribute('data-bee'))).toEqual(['warden_1', 'w_1', 'w_2']);
    for (const node of nodes) {
      expect(node.querySelector('title')?.textContent).toBe(node.getAttribute('aria-label'));
    }
    expect(nodes[1]!.getAttribute('href')).toBe('#/thoughts/w_1');
    expect(nodes[0]!.getAttribute('href')).toBe('#/attendant/warden_1');
    expect(nodes[2]!.querySelector('title')?.textContent).toContain('Forager w_2');
  });

  it('draws the Warden inside the frame when it runs on the Cell and outside otherwise', () => {
    const yOf = (container: HTMLElement, sel: string) =>
      Number(container.querySelector(sel)!.getAttribute('y'));
    const onCell = draw({ warden_runs: 'on_cell' }).container;
    // The shapes are paths, so each carries its top edge in `data-y` for the test to read.
    const top = (c: HTMLElement, sel: string) =>
      Number(c.querySelector(sel)!.getAttribute('data-y'));
    expect(top(onCell, '[data-bee="warden_1"] .dg-shape')).toBeGreaterThan(
      top(onCell, 'svg > .dg-frame'),
    );
    const onStand = draw({ warden_runs: 'hive_stand' }).container;
    expect(top(onStand, '[data-bee="warden_1"] .dg-shape') + 88).toBeLessThanOrEqual(
      top(onStand, 'svg > .dg-frame'),
    );
    expect(yOf(onStand, '[data-bee="warden_1"] .dg-line')).toBeGreaterThan(0);
  });

  it('says "not tracked" and dashes the Warden when warden_runs is null', () => {
    const { container } = draw({ warden_runs: null });
    const warden = container.querySelector('[data-bee="warden_1"]')!;
    expect(warden.textContent).toContain('not tracked');
    expect(warden.querySelector('.dg-shape')!.getAttribute('stroke-dasharray')).toBeTruthy();
  });

  it('gives each bee a context meter with its numbers, and none when unreported', () => {
    const { container } = draw({
      bees: [
        wardenRow(),
        beeRow({
          id: 'w_1',
          context_tokens_used: 90000,
          context_window: 100000,
          context_fraction: 0.9,
        }),
        beeRow({
          id: 'w_2',
          context_tokens_used: null,
          context_window: null,
          context_fraction: null,
        }),
      ],
    });
    const meter = container.querySelector('[data-bee="w_1"] [role="meter"]')!;
    expect(meter.getAttribute('aria-valuenow')).toBe('90000');
    expect(meter.getAttribute('aria-valuemax')).toBe('100000');
    // The percent is drawn beside the bar, not left to the fill or a hover.
    expect(meter.textContent).toContain('90%');
    expect(container.querySelector('[data-bee="w_2"] [role="meter"]')).toBeNull();
    expect(container.querySelector('[data-bee="w_2"]')!.getAttribute('aria-label')).toContain(
      'context not reported',
    );
  });

  it('outlines an Alarm bee in the alarm colour with the word, and an awake bee in the accent', () => {
    const { container } = draw(
      {
        bees: [
          wardenRow(),
          beeRow({ id: 'w_1', task_id: 't_1' }),
          beeRow({ id: 'w_2', task_id: 't_2' }),
        ],
        alarms: [alarmView({ task_id: 't_1' })],
      },
      new Set(['w_2']),
    );
    const outline = (id: string) =>
      container.querySelector(`[data-bee="${id}"] .dg-shape`)!.getAttribute('stroke');
    expect(outline('w_1')).toBe('var(--alarm)');
    expect(container.querySelector('[data-bee="w_1"]')!.textContent).toContain('Alarm');
    expect(outline('w_2')).toBe('var(--accent)');
    expect(container.querySelector('[data-bee="w_2"] .dg-awake')!.textContent).toBe('awake');
    expect(container.querySelector('[data-bee="w_1"] .dg-awake')).toBeNull();
    expect(outline('warden_1')).toBe('var(--border)');
  });

  it('shows the fact boxes, each with its own title', () => {
    const { container } = draw({ exoskeleton_attached: true });
    const boxes = [...container.querySelectorAll('[data-box]')];
    expect(boxes.map((b) => b.getAttribute('data-box'))).toEqual([
      'session',
      'exoskeleton',
      'lease',
      'hosting',
    ]);
    expect(container.querySelector('[data-box="exoskeleton"]')!.textContent).toContain('attached');
    expect(container.querySelector('[data-box="hosting"] title')!.textContent).toContain('hybrid');
  });

  it('says so inside the frame when there is no Warden or no sub-bee', () => {
    expect(
      draw({ bees: [], warden: null, cell: cellRecord({ warden_id: null }) }).container.textContent,
    ).toContain('No Warden attached');
    expect(draw({ bees: [wardenRow()] }).container.textContent).toContain('No sub-bees right now');
  });

  it('uses tokens for every colour, never a literal', () => {
    const { container } = draw({ alarms: [alarmView({ task_id: 'task_1' })] });
    const html = container.innerHTML;
    expect(html).not.toMatch(/(fill|stroke)="#/);
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}["';\s]/);
    expect(html).toContain('var(--');
  });

  it('repeats its numbers in a hidden table', () => {
    const { container } = draw();
    const table = container.querySelector('.sr-only table')!;
    expect(table.textContent).toContain('worker_1');
    expect(table.textContent).toContain('Exoskeleton');
  });

  it('renders byte-identically twice, narrow and wide', () => {
    const first = draw().container.innerHTML;
    expect(draw().container.innerHTML).toBe(first);
    stubWideContainer(900);
    const wide = draw().container.innerHTML;
    expect(draw().container.innerHTML).toBe(wide);
    expect(wide).not.toBe(first);
  });

  it('switches to the wide viewBox when its container is wide', () => {
    stubWideContainer(900);
    const { container } = draw();
    expect(container.querySelector('svg')!.getAttribute('viewBox')).toMatch(/^0 0 744 /);
  });
});
