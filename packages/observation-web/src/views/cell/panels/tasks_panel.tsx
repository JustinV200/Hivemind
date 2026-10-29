/**
 * `TasksPanel`: the Cell's current tasks, grouped by goal, with links to the bees behind them.
 *
 * Each goal is a heading (with a link to the chat when the goal was asked for there, since the
 * page carries no goal title) and its tasks a list: the task id, its status in words, the attempt,
 * the Worker's own progress estimate, the shield level it is bound to and the question it waits on
 * while blocked. A task a bee is working on links to that bee's thoughts. Below the list, the Warden's
 * Attendant view and every bee whose thoughts can be opened. A task id the Cell frame names but the
 * page does not list yet (a placement that landed since the last read) shows as a bare id.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx` from the page.
 *
 * Key invariants:
 *   - Goals sort by id and tasks by id, so the order never depends on arrival.
 *   - Only schema fields are shown; a task's title is not on the page (C2) and is not invented.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2.
 */
import type { ReactElement } from 'react';
import { IdToken } from '../../../components';
import { hrefFor } from '../../../router';
import type { CellPage } from '../slice';
import { Panel, Tag } from './panel';
import type { TagTone } from './panel';

type Task = CellPage['tasks'][number];

const STATUS_TONE: Readonly<Record<Task['status'], TagTone>> = {
  PENDING: 'idle',
  ASSIGNED: 'idle',
  RUNNING: 'ok',
  BLOCKED: 'warn',
  PAUSED: 'warn',
  SUCCEEDED: 'ok',
  FAILED: 'alarm',
  CANCELLED: 'idle',
};

// Goals by id, each with its tasks by id.
function byGoal(tasks: readonly Task[]): [string, Task[]][] {
  const goals = new Map<string, Task[]>();
  for (const task of tasks) goals.set(task.goal_id, [...(goals.get(task.goal_id) ?? []), task]);
  return [...goals.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([goal, list]) => [goal, list.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))]);
}

function TaskRow(props: { task: Task; bee: string | null }): ReactElement {
  const { task, bee } = props;
  return (
    <li className="cell-row">
      <IdToken id={task.id} />
      <Tag tone={STATUS_TONE[task.status]}>{task.status.toLowerCase()}</Tag>
      {task.attempt > 1 ? <span>{`attempt ${task.attempt}`}</span> : null}
      {task.fraction_done !== null ? (
        <span>{`${Math.round(task.fraction_done * 100)}% done`}</span>
      ) : null}
      {task.bound_tier ? <span>{`shield level ${task.bound_tier.replace('_', ' ')}`}</span> : null}
      {task.blocked_on ? <span>{`waiting on: ${task.blocked_on}`}</span> : null}
      {bee ? <a href={hrefFor({ name: 'thoughts', bee })}>{`thoughts of ${bee}`}</a> : null}
    </li>
  );
}

export function TasksPanel(props: { page: CellPage }): ReactElement {
  const { page } = props;
  const listed = new Set(page.tasks.map((task) => task.id));
  const unlisted = page.cell.current_tasks.filter((id) => !listed.has(id));
  const beeOf = (taskId: string): string | null =>
    page.bees.find((bee) => bee.task_id === taskId)?.id ?? null;
  const wardenId = page.warden?.id ?? null;
  const empty = page.tasks.length === 0 && unlisted.length === 0;
  return (
    <Panel id="tasks" title="Tasks and goals">
      {empty ? <p>No tasks on this Cell. One appears here when the Queen places it.</p> : null}
      {byGoal(page.tasks).map(([goal, tasks]) => (
        <div key={goal} className="cell-goal">
          <h3>
            Goal <IdToken id={goal} />
          </h3>
          {/* A goal has no title on the page; the chat holds the words it was asked in. */}
          {tasks[0]?.goal_request_id ? (
            <a
              className="cell-muted"
              href={hrefFor({ name: 'chat' })}
              title={`Planned from request ${tasks[0].goal_request_id}`}
            >
              asked in the chat
            </a>
          ) : null}
          <ul className="cell-list">
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} bee={beeOf(task.id)} />
            ))}
          </ul>
        </div>
      ))}
      {unlisted.length > 0 ? (
        <ul className="cell-list">
          {unlisted.map((id) => (
            <li key={id} className="cell-row">
              <IdToken id={id} />
              <span className="cell-muted">placed just now</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="cell-links">
        {wardenId ? (
          <a href={hrefFor({ name: 'attendant', principal: wardenId })}>
            {`Attendant view of ${wardenId}`}
          </a>
        ) : null}
        {page.thought_principals.map((bee) => (
          <a key={bee} href={hrefFor({ name: 'thoughts', bee })}>
            {`Thoughts of ${bee}`}
          </a>
        ))}
      </p>
    </Panel>
  );
}
