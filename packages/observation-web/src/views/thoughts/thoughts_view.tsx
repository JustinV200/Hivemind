/**
 * The Thoughts view: a bee's episodes as they happen, newest first, at `#/thoughts/<bee>`.
 *
 * It shows what a bee decided and why: each episode's trigger, decision, action and reasoning
 * summary, with the rest of its record in sections that open in place. For the Queen it adds the
 * Attendant's current ordering of her inbox and her autopilot decisions, interleaved with the
 * episodes in time order; her decisions stay live on two trail streams, one per decision kind. Follow (on by default) streams new episodes to the top; off, they queue
 * behind an "N new" button so the list does not move. "Older" pages back through history. The
 * route is the same for every bee, so the fleet can link to any of them.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Chosen by the outlet in `src/shell/` when
 *   the route is `thoughts`. Seeds by `GET /v1/thoughts`, stays live on `/v1/episodes/stream` (and, for the
 *   Queen, `/v1/trail/stream` for `queen.decided` and `alarm.handled`).
 *
 * Key invariants:
 *   - Read-only: it reads one page, one inbox and its streams, and writes only its own slice.
 *   - A refusal (no `observe:thoughts`) is shown as the error table's sentence, never as an
 *     empty list.
 *   - Everything drawn comes from the generated types; secrets are redacted at the source.
 *
 * See Also:
 *   - docs/observation/design.md section 5.4.
 *   - src/views/thoughts/README.md.
 */
import type { ReactElement } from 'react';
import { EmptyState, ErrorNotice, VIEW_HINTS, ViewTitle } from '../../components';
import { useHive, useViewStream } from '../../store';
import { DecisionRow } from './decision_row';
import { EpisodeCard } from './episode_card';
import { FollowToggle } from './follow_toggle';
import { InboxPanel } from './inbox_panel';
import { MAX_EPISODES, QUEEN } from './slice';
import type { EpisodeFrame, ThoughtsEvent, TrailFrame } from './slice';
import { timelineOf } from './timeline';
import { useThoughts } from './use_thoughts';

export const EPISODES_STREAM = '/v1/episodes/stream';

// Module-level so the stream is opened once per mount, not once per render.
const toEvent = (frame: EpisodeFrame): ThoughtsEvent => ({ type: 'frame', frame });
const toDecision = (frame: TrailFrame): ThoughtsEvent => ({ type: 'decision', frame });

// The Queen's decisions have no episodes: each trail kind is its own filtered stream, opened
// only while her thoughts are on screen (a hook cannot be conditional, so a component is).
export const DECIDED_STREAM = '/v1/trail/stream?kind=queen.decided';
export const HANDLED_STREAM = '/v1/trail/stream?kind=alarm.handled';

function DecisionStreams(): null {
  useViewStream(DECIDED_STREAM, 'thoughts', toDecision);
  useViewStream(HANDLED_STREAM, 'thoughts', toDecision);
  return null;
}

export function ThoughtsView(props: { bee: string }): ReactElement {
  const { store } = useHive();
  const { state, loadingOlder, loadOlder } = useThoughts(props.bee);
  useViewStream(EPISODES_STREAM, 'thoughts', toEvent);

  const items = timelineOf(state.episodes, state.decisions);
  return (
    <>
      <ViewTitle title="Thoughts" subject={props.bee} hint={VIEW_HINTS.thoughts}>
        <FollowToggle
          follow={state.follow}
          queued={state.queued.length + state.queuedDecisions.length}
          onFollow={(on) => store.dispatch({ slice: 'thoughts', event: { type: 'follow', on } })}
          onReveal={() => store.dispatch({ slice: 'thoughts', event: { type: 'reveal' } })}
        />
      </ViewTitle>
      {props.bee === QUEEN ? <DecisionStreams /> : null}
      {props.bee === QUEEN ? <InboxPanel /> : null}
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {!state.seeded && !state.error ? <p aria-busy="true">Reading thoughts</p> : null}
      {state.seeded && items.length === 0 ? (
        <EmptyState>
          No thoughts yet. An episode appears here when this bee wakes to decide something.
        </EmptyState>
      ) : null}
      <div className="thought-list">
        {items.map((item) =>
          item.kind === 'episode' ? (
            <EpisodeCard key={`e:${item.episode.id}`} episode={item.episode} />
          ) : (
            <DecisionRow key={`d:${item.decision.id}`} decision={item.decision} />
          ),
        )}
      </div>
      {state.nextCursor !== null ? (
        <button type="button" className="button" disabled={loadingOlder} onClick={loadOlder}>
          Older
        </button>
      ) : null}
      {state.truncated ? (
        <p className="thought-note">
          The newest {MAX_EPISODES} are kept; open this view again to read further back.
        </p>
      ) : null}
    </>
  );
}
