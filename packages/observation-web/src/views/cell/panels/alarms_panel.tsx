/**
 * `AlarmsPanel`: the Alarms open on this Cell, with their severity and whether they reached the
 * human.
 *
 * An Alarm is what a bee raises when something went wrong (`WORKER_CRASHED`, `GRANT_EXCEEDED`).
 * Each row shows its severity in words, its kind as a plain phrase (the code in its `title`),
 * its id, the task it concerns and when it was raised, and says so when it has escalated to the
 * human.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx` from the page; the
 *   list is refreshed by the page's trail-triggered re-read.
 *
 * Key invariants:
 *   - Rows are the page's open Alarms in its order; a resolved Alarm is not on the page.
 *
 * See Also:
 *   - docs/observation/design.md section 5.2.
 */
import type { ReactElement } from 'react';
import { alarmKindWords, IdToken, TimeStamp } from '../../../components';
import type { CellPage } from '../slice';
import { Panel, Tag } from './panel';
import type { TagTone } from './panel';

type Alarm = CellPage['alarms'][number];

const SEVERITY_TONE: Readonly<Record<Alarm['severity'], TagTone>> = {
  INFO: 'idle',
  WARNING: 'warn',
  CRITICAL: 'alarm',
};

export function AlarmsPanel(props: { page: CellPage }): ReactElement {
  const { alarms } = props.page;
  return (
    <Panel id="alarms" title="Alarms">
      {alarms.length === 0 ? <p>No open Alarms on this Cell.</p> : null}
      <ul className="cell-list">
        {alarms.map((alarm) => (
          <li key={alarm.id} className="cell-row">
            <Tag tone={SEVERITY_TONE[alarm.severity]}>{alarm.severity}</Tag>
            <span title={alarm.kind}>{alarmKindWords(alarm.kind)}</span>
            <IdToken id={alarm.id} />
            {alarm.task_id ? (
              <span>
                task <IdToken id={alarm.task_id} />
              </span>
            ) : null}
            <span>
              raised <TimeStamp at={alarm.raised_at} />
            </span>
            {alarm.escalated ? <Tag tone="warn">reached the human</Tag> : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
