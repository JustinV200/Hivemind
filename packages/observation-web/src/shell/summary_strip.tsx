/**
 * The summary strip: the Hive's core counters, always visible under the top bar.
 *
 * Four groups: active bees, Cells active and inactive, Real and Virtual Cells, and bees thinking
 * with an LLM versus on autopilot. The numbers come from the summary slice and never wait on the
 * view the person is looking at. If any stream that could be live is not, the strip dims and says
 * `stale since hh:mm:ss`, so old numbers are never mistaken for current ones. A group whose
 * stream the Entrance refused says `no access` instead of a number. Before the first data arrives
 * the strip shows dashes and `aria-busy`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Rendered by `shell.tsx` inside the sticky head.
 *
 * Key invariants:
 *   - A `role="status"` region with `aria-live="off"`: it changes constantly and must not chatter.
 *   - Reads slices only; it opens no stream and makes no request.
 *   - The time in `stale since` is the moment the first needed stream stopped being live.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 7.
 *   - src/store/slices/summary.ts and src/store/slices/connection.ts for the data.
 */
import type { ReactElement } from 'react';
import { refusedStreams, staleSince, summaryCounters, useSlice } from '../store';
import type { StreamName, SummaryCounters } from '../store';

interface Group {
  label: string;
  hint: string;
  value: string;
  spoken: string;
  needs: readonly StreamName[];
}

function groupsOf(c: SummaryCounters | null): Group[] {
  const show = (text: string | number): string => (c ? String(text) : '-');
  return [
    {
      label: 'Bees',
      hint: 'active',
      value: show(c?.active_bees ?? 0),
      spoken: `${c?.active_bees ?? 0} active`,
      needs: ['telemetry'],
    },
    {
      label: 'Cells',
      hint: 'active / inactive',
      value: show(`${c?.cells_active} / ${c?.cells_inactive}`),
      spoken: `${c?.cells_active} active, ${c?.cells_inactive} inactive`,
      needs: ['cells'],
    },
    {
      label: 'Kind',
      hint: 'Real / Virtual',
      value: show(`${c?.cells_real} / ${c?.cells_virtual}`),
      spoken: `${c?.cells_real} Real, ${c?.cells_virtual} Virtual`,
      needs: ['cells'],
    },
    {
      label: 'Thinking',
      hint: 'LLM / autopilot',
      value: show(`${c?.bees_awake} / ${c?.bees_autopilot}`),
      spoken: `${c?.bees_awake} LLM, ${c?.bees_autopilot} autopilot`,
      needs: ['episodes'],
    },
  ];
}

function clock(ms: number): string {
  const date = new Date(ms);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function SummaryStrip(): ReactElement {
  const summary = useSlice('summary');
  const connection = useSlice('connection');
  const counters = summaryCounters(summary);
  const refused = refusedStreams(connection);
  const since = staleSince(connection);
  const stale = counters !== null && since !== null;

  return (
    <div
      className={`strip${stale ? ' strip--stale' : ''}`}
      role="status"
      aria-live="off"
      aria-busy={counters === null}
    >
      {groupsOf(counters).map((group) => {
        const denied = group.needs.some((name) => refused.includes(name));
        return (
          <div
            key={group.label}
            className="strip-group"
            role="group"
            aria-label={`${group.label}: ${denied ? 'no access' : counters ? group.spoken : 'loading'}`}
          >
            <span className="strip-label">
              {group.label} <span>{group.hint}</span>
            </span>
            <span className="strip-value">{denied ? 'no access' : group.value}</span>
          </div>
        );
      })}
      {stale && since !== null ? (
        <span className="strip-stale">stale since {clock(since)}</span>
      ) : null}
    </div>
  );
}
