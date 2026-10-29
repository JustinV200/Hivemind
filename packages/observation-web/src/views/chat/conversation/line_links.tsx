/**
 * `LineLinks`: the links from one chat line to what it produced or concerns.
 *
 * The schema ties a chat line to a task by `task_id`, so a line that names one links to the
 * Queen's Attendant view, where the task graph and its placement are; the link says so in words
 * ("See task ... in the Attendant"), since no task page exists yet, and carries the message the
 * line answers as its `title`, because a task has no title of its own. A message the human wrote
 * is what the Queen reads in her next episode, so it also links to her thoughts, where that
 * episode is recorded. The Landing Board does not name the episodes a line produced, so the link
 * goes to the list and not to one record.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `line_row.tsx` and by
 *   the waiting items; the hashes come from the router's `hrefFor`.
 *
 * Key invariants:
 *   - A link appears only where the schema gives a reason for it (a `task_id`, or a human line).
 *   - Nothing here makes a request.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6.
 *   - src/router/routes.ts for the routes linked to.
 */
import type { ReactElement } from 'react';
import { IdToken } from '../../../components';
import { hrefFor } from '../../../router';

export function LineLinks(props: {
  taskId: string | null;
  fromHuman: boolean;
  // The words of the message the line answers, when the slice knows them.
  about?: string | null;
}): ReactElement | null {
  const { taskId, fromHuman, about } = props;
  if (taskId === null && !fromHuman) return null;
  // The Landing Board names a task by id only, so the message it came from is the best title.
  const title = about ? `About: ${about}` : undefined;
  return (
    <p className="chat-links">
      {taskId !== null ? (
        <a href={hrefFor({ name: 'attendant', principal: 'queen' })} title={title}>
          See task <IdToken id={taskId} /> in the Attendant
        </a>
      ) : null}
      {fromHuman ? (
        <a href={hrefFor({ name: 'thoughts', bee: 'queen' })}>Queen&apos;s thoughts</a>
      ) : null}
    </p>
  );
}
