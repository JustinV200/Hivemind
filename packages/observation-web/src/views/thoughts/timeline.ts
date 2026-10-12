/**
 * The timeline: episodes and autopilot decisions merged into one list in time order.
 *
 * The Thoughts view shows what a bee decided and what the Queen's autopilot decided as one story,
 * newest first. Both inputs already arrive newest first, so a single merge pass is enough and the
 * order is deterministic: on equal times an episode comes before a decision.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Called by `thoughts_view.tsx` on the slice.
 *
 * Key invariants:
 *   - Pure; the inputs are not changed and every input item appears once in the output.
 *   - Items are the generated types as received; a timeline entry only tags which kind it is.
 *
 * See Also:
 *   - docs/observation/design.md section 5.4 (decisions interleave with episodes).
 */
import type { DecisionView, EpisodeView } from './slice';

export type TimelineItem =
  | { kind: 'episode'; at: string; episode: EpisodeView }
  | { kind: 'decision'; at: string; decision: DecisionView };

export function timelineOf(
  episodes: readonly EpisodeView[],
  decisions: readonly DecisionView[],
): TimelineItem[] {
  const items: TimelineItem[] = [];
  let e = 0;
  let d = 0;
  while (e < episodes.length || d < decisions.length) {
    const episode = episodes[e];
    const decision = decisions[d];
    // Take the newer head; a tie goes to the episode, so a decision follows the episode it matches.
    const takeEpisode =
      episode !== undefined &&
      (decision === undefined || Date.parse(episode.at) >= Date.parse(decision.at));
    if (takeEpisode) {
      items.push({ kind: 'episode', at: episode.at, episode });
      e += 1;
    } else if (decision !== undefined) {
      items.push({ kind: 'decision', at: decision.at, decision });
      d += 1;
    }
  }
  return items;
}
