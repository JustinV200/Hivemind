/**
 * Words and colours for a task node: its status tone, its link and its one-line description.
 *
 * Status is never told by colour alone: the tone picks a token for the node's outline and dot,
 * and the status word is always written beside it. The same description feeds the node's
 * `<title>`, its `aria-label` and the hidden table, so what a screen reader hears is what the
 * node says.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Used by `task_node.tsx`,
 *   `graph_table.tsx` and `lists/task_lists.tsx`.
 *
 * Key invariants:
 *   - Every `TaskStatus` has a tone; a status the schema adds later fails to compile here.
 *   - The link goes to the bee when the node names one (its Warden), else to the task's Cell page,
 *     else nowhere: an unplaced task has no page to open.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.2 and 4.8.
 */
import { plainCode } from '../../../components';
import type { components } from '../../../landing_board/types';
import { hrefFor } from '../../../router';

type Schemas = components['schemas'];
export type TaskNode = Schemas['TaskNode'];
export type TaskStatus = Schemas['TaskStatus'];
export type Tone = 'ok' | 'warn' | 'alarm' | 'idle';

const TONE: Readonly<Record<TaskStatus, Tone>> = {
  PENDING: 'idle',
  ASSIGNED: 'idle',
  RUNNING: 'ok',
  BLOCKED: 'warn',
  PAUSED: 'warn',
  SUCCEEDED: 'ok',
  FAILED: 'alarm',
  CANCELLED: 'idle',
};

export function toneOf(status: TaskStatus): Tone {
  return TONE[status];
}

// The token a tone is drawn in, as an SVG paint value.
export function paintOf(tone: Tone): string {
  return `var(--${tone})`;
}

// The status as every view writes a code (design.md 8); typed so a non-status cannot be passed.
export function statusWord(status: TaskStatus): string {
  return plainCode(status);
}

export function placementOf(node: TaskNode): string {
  return node.cell_id === null ? 'not placed yet' : `on ${node.cell_id}`;
}

// Where a click on the node goes: the bee that works it, else its Cell; null while unplaced.
export function hrefOfNode(node: TaskNode): string | null {
  if (node.warden_id !== null) return hrefFor({ name: 'thoughts', bee: node.warden_id });
  if (node.cell_id !== null) return hrefFor({ name: 'cell', id: node.cell_id });
  return null;
}

// The node in words, for its title, its label and the hidden table.
export function describeNode(node: TaskNode): string {
  const parts = [
    `Task ${node.task_id}`,
    statusWord(node.status),
    `role ${plainCode(node.role)}`,
    placementOf(node),
  ];
  if (node.warden_id !== null) parts.push(`Warden ${node.warden_id}`);
  const alarms = node.open_alarms;
  parts.push(alarms === 0 ? 'no open Alarms' : `${alarms} open Alarm${alarms === 1 ? '' : 's'}`);
  return parts.join(', ');
}
