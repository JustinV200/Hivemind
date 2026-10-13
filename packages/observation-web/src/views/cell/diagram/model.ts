/**
 * The diagram's content: what each node and box says, derived from the page, with no geometry.
 *
 * `buildModel` turns the Cell page into plain data: the Warden node and the sub-bee nodes (role,
 * status word and tone, what it is doing, context fraction, awake or Alarm outline) and the four
 * fact boxes (session, Exoskeleton, lease or Virtual Cell, model hosting). `layout.ts` then places
 * them and `cell_diagram.tsx` draws them, so the words can be tested without React or pixels.
 * Text that the SVG must cut to fit is cut here, with the whole text kept for the `<title>`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Called by `cell_diagram.tsx` with the
 *   slice's page and the set of awake bees from the summary slice.
 *
 * Key invariants:
 *   - Pure and deterministic: sub-bees sort by role then id with a plain string compare, so two
 *     builds from the same page are equal.
 *   - A field the schema leaves null reads as "not tracked" or "unknown", never as a guess.
 *   - Colour is a tone name here (`ok`, `warn`, `alarm`, `idle`); the tokens are chosen in the
 *     renderer, so this file holds no colour.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.5 and 4.6.
 */
import type { BeeRow, CellPage } from '../slice';
import { roleLook } from './glyphs';
import type { GlyphKey } from './glyphs';

export type Tone = 'ok' | 'warn' | 'alarm' | 'idle';
export type WardenRuns = 'hive_stand' | 'on_cell' | null;

export interface BeeNode {
  id: string;
  isWarden: boolean;
  glyph: GlyphKey;
  roleLabel: string;
  // The fixed status word (working, idle, waiting, failed, watch) and the tone of its dot.
  word: string;
  tone: Tone;
  // The line under the id: what it is doing (a task id) or, for a Warden, where it runs.
  line: string;
  taskId: string | null;
  used: number | null;
  window: number | null;
  fraction: number | null;
  awake: boolean;
  alarmed: boolean;
  // The whole text of the node, for the `<title>` and the screen-reader table.
  title: string;
}

export interface FactBox {
  key: 'session' | 'exoskeleton' | 'lease' | 'hosting';
  label: string;
  value: string;
  detail: string;
  title: string;
}

export interface DiagramModel {
  cellId: string;
  frameLabel: string;
  wardenRuns: WardenRuns;
  warden: BeeNode | null;
  bees: BeeNode[];
  boxes: FactBox[];
  summary: string;
}

// Characters a one-line cut keeps; the SVG text has no room for more (design.md 4.5).
export const ID_CUT = 18;
export const LINE_CUT = 24;

export function cut(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

// A Warden or Worker state, as the bee reported it, to the fixed word and tone (design.md 4.4).
const STATUS: Readonly<Record<string, readonly [string, Tone]>> = {
  RUNNING: ['working', 'ok'],
  SPAWNED: ['working', 'ok'],
  ACTIVE: ['working', 'ok'],
  STARTING: ['waiting', 'warn'],
  HANDING_OFF: ['waiting', 'warn'],
  PAUSED: ['waiting', 'warn'],
  CLUSTERED: ['waiting', 'warn'],
  MIGRATING: ['waiting', 'warn'],
  WATCH: ['watch', 'idle'],
  DONE: ['idle', 'idle'],
  STOPPED: ['idle', 'idle'],
  FAILED: ['failed', 'alarm'],
  KILLED: ['failed', 'alarm'],
  OFFLINE: ['failed', 'alarm'],
};

const RUNS_LINE: Readonly<Record<'hive_stand' | 'on_cell', string>> = {
  hive_stand: 'Hive Stand',
  on_cell: 'on the Cell',
};

// "1h 05m", "4m 10s" or "12s": a lease's age at the precision a reader acts on.
export function ageText(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return m > 0 ? `${m}m ${String(total % 60).padStart(2, '0')}s` : `${total}s`;
}

function nodeOf(bee: BeeRow, runs: WardenRuns, alarmTasks: ReadonlySet<string>, awake: boolean) {
  const look = roleLook(bee.role);
  const isWarden = bee.role === 'WARDEN';
  const [word, tone] = STATUS[bee.status] ?? [bee.status.toLowerCase(), 'idle'];
  const where = runs === null ? 'not tracked' : RUNS_LINE[runs];
  const line = isWarden ? where : (bee.task_id ?? 'no task');
  const alarmed = bee.task_id !== null && alarmTasks.has(bee.task_id);
  const context =
    bee.context_tokens_used !== null && bee.context_window !== null
      ? `context ${bee.context_tokens_used} of ${bee.context_window}`
      : 'context not reported';
  const doing = isWarden ? `runs ${where}` : `doing ${bee.task_id ?? 'nothing'}`;
  const parts = [`${look.label} ${bee.id}`, doing, `status ${word}`, context];
  if (alarmed) parts.push('an open Alarm names its task');
  if (awake) parts.push('in an awake episode');
  const node: BeeNode = {
    id: bee.id,
    isWarden,
    glyph: look.glyph,
    roleLabel: look.label,
    word,
    tone,
    line,
    taskId: bee.task_id,
    used: bee.context_tokens_used,
    window: bee.context_window,
    fraction: bee.context_fraction,
    awake,
    alarmed,
    title: parts.join(', '),
  };
  return node;
}

const EXO: Readonly<Record<'true' | 'false' | 'null', string>> = {
  true: 'attached',
  false: 'not attached',
  null: 'unknown',
};

const HOSTING: Readonly<Record<string, string>> = {
  local: 'local',
  hive_stand: 'Hive Stand',
  hosted: 'hosted',
  hybrid: 'hybrid',
  unknown: 'unknown',
};

function boxesOf(page: CellPage): FactBox[] {
  const { cell } = page;
  const exo = EXO[String(page.exoskeleton_attached) as 'true' | 'false' | 'null'];
  const real = cell.kind === 'REAL';
  const leaseValue = cell.lease_state ?? 'no lease yet';
  const age = page.lease_age_s === null ? 'age not reported' : `age ${ageText(page.lease_age_s)}`;
  const virtualValue = cell.virtual_status ?? 'not tracked';
  const slots = page.hosting.slots.map((s) => `${s.slot} ${HOSTING[s.models_from]}`);
  const plan = page.hosting.plan_revision;
  const hostingDetail = plan === null ? 'no plan' : `plan revision ${plan}`;
  return [
    {
      key: 'session',
      label: 'Session',
      value: 'not tracked yet',
      detail: '',
      title: 'Session: the Cell session kind and state are not in the page yet',
    },
    {
      key: 'exoskeleton',
      label: 'Exoskeleton',
      value: exo,
      detail: '',
      title: `Exoskeleton: ${exo}`,
    },
    {
      key: 'lease',
      label: real ? 'Lease' : 'Virtual Cell',
      value: real ? leaseValue : virtualValue,
      detail: real ? age : `backend ${cell.source}`,
      title: real
        ? `Lease: ${leaseValue}, ${age}`
        : `Virtual Cell: ${virtualValue}, backend ${cell.source}`,
    },
    {
      key: 'hosting',
      label: 'Models',
      value: HOSTING[page.hosting.models_from] ?? 'unknown',
      detail: hostingDetail,
      title: `Models from ${HOSTING[page.hosting.models_from]}, ${hostingDetail}: ${slots.join(', ') || 'no slots planned'}`,
    },
  ];
}

// Bees in an awake (LLM) episode, from the summary slice's per-principal latest episode.
export function awakeSet(latest: Readonly<Record<string, { auto: boolean }>>): ReadonlySet<string> {
  return new Set(Object.keys(latest).filter((id) => latest[id]?.auto === false));
}

function byRoleThenId(a: BeeRow, b: BeeRow): number {
  if (a.role !== b.role) return a.role < b.role ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

export function buildModel(page: CellPage, awake: ReadonlySet<string>): DiagramModel {
  const { cell } = page;
  const alarmTasks = new Set(page.alarms.flatMap((a) => (a.task_id === null ? [] : [a.task_id])));
  const runs = page.warden_runs;
  const make = (bee: BeeRow): BeeNode => nodeOf(bee, runs, alarmTasks, awake.has(bee.id));
  const wardenRow = page.bees.find((b) => b.role === 'WARDEN' || b.id === cell.warden_id);
  const subs = page.bees.filter((b) => b !== wardenRow).sort(byRoleThenId);
  const kind = cell.kind === 'REAL' ? 'Real Cell' : 'Virtual Cell';
  return {
    cellId: cell.id,
    frameLabel: cell.os ? `${kind} · ${cell.os}` : kind,
    wardenRuns: runs,
    warden: wardenRow ? make(wardenRow) : null,
    bees: subs.map(make),
    boxes: boxesOf(page),
    summary: `${kind} ${cell.id}: ${subs.length} sub-bees, Warden ${
      runs === null ? 'location not tracked' : RUNS_LINE[runs]
    }`,
  };
}
