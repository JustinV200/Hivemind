/**
 * `EpisodeCard`: one episode record as a card, with its context in sections that expand inline.
 *
 * An episode is one decision: what triggered it (its kind as a plain phrase in a header tag, its
 * summary as the first line), what was decided and what was done, with the provider's reasoning summary when
 * it gave one. What a record carries beyond that is grouped into
 * sections (the assembled prompt it refers to, the trigger's detail, the usage), each collapsed
 * until the reader opens it, one at a time, in place. A section appears only when the record
 * carries something for it; the card never shows a field the schema does not have. An autopilot
 * decision is marked, as it ran no model and so has no usage.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Drawn by `thoughts_view.tsx` for each
 *   episode in the timeline.
 *
 * Key invariants:
 *   - Read-only: the card holds only the open or closed state of its sections.
 *   - Text is rendered as text, never as markup; the Entrance has already redacted secrets and
 *     screenshot bytes at the source, so the card shows what the record says and fetches no more.
 *   - Ids use the monospace token and the clearance label is always visible.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 5.4.
 */
import { useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import {
  CLEARANCE_MEANINGS,
  clearanceWords,
  IdToken,
  plainCode,
  TimeStamp,
} from '../../components';
import type { EpisodeView } from './slice';

// What woke the bee, as a person would say it; a kind not listed reads as sent, in plain case.
const TRIGGER_WORDS: Readonly<Record<string, string>> = {
  tick: 'a timer tick',
  message: 'a message',
  alarm: 'an Alarm',
  question: 'a question',
  result: 'a task result',
  heartbeat: 'a heartbeat',
  watch: 'a watch observation',
};

const triggerWords = (kind: string): string => TRIGGER_WORDS[kind] ?? plainCode(kind);

// Why a tainted or cleared thought is marked, as the tag's `title`.
const TAINT_MEANING = {
  tainted: 'This thought read text that may have carried instructions; the source is named.',
  cleared: 'A judge cleared text that could have carried instructions; the source is named.',
} as const;

// A disclosure built on a button, so it works the same in every browser and is plainly labelled.
function Section(props: { title: string; children: ReactNode }): ReactElement {
  const [open, setOpen] = useState(false);
  return (
    <section className="thought-section">
      <h4>
        <button
          type="button"
          className="thought-section-toggle"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {props.title}
        </button>
      </h4>
      {open ? <div className="thought-section-body">{props.children}</div> : null}
    </section>
  );
}

function Pair(props: { label: string; children: ReactNode }): ReactElement {
  return (
    <>
      <dt>{props.label}</dt>
      <dd>{props.children}</dd>
    </>
  );
}

const count = (n: number): string => n.toLocaleString('en-US');

// Usage is real only for a model call; an autopilot decision has no tokens and no cost.
function Usage(props: { episode: EpisodeView }): ReactElement {
  const { slot, input_tokens, output_tokens, cost_usd } = props.episode;
  return (
    <dl className="thought-pairs">
      <Pair label="Model slot">
        <span title={slot}>{plainCode(slot)}</span>
      </Pair>
      {input_tokens !== null ? <Pair label="Input tokens">{count(input_tokens)}</Pair> : null}
      {output_tokens !== null ? <Pair label="Output tokens">{count(output_tokens)}</Pair> : null}
      {cost_usd !== null ? <Pair label="Cost">{`$${cost_usd.toFixed(4)}`}</Pair> : null}
    </dl>
  );
}

// A labelled record is marked where the clearance is, so nobody reads a tainted thought without
// seeing it; a cleared one keeps its history visible too (codingrules 8.11). Taint is a status,
// so the tag takes the status tones: refused reads as an alarm, cleared as a caution whose
// history is kept; the word carries the meaning. Null renders nothing.
function TaintTag({ episode }: { episode: EpisodeView }): ReactElement | null {
  if (episode.taint === null) return null;
  const source = episode.taint_source !== null ? ` (${plainCode(episode.taint_source)})` : '';
  const tainted = episode.taint === 'tainted';
  return (
    <span
      className={`tag tag--status tag--${tainted ? 'alarm' : 'warn'}`}
      title={tainted ? TAINT_MEANING.tainted : TAINT_MEANING.cleared}
    >
      {plainCode(episode.taint)}
      {source}
    </span>
  );
}

// The card's header: when, which, and the tags (how it decided, its clearance, taint, trigger),
// each with a plain phrase and the schema's code on its `title`.
function Head(props: { episode: EpisodeView }): ReactElement {
  const e = props.episode;
  return (
    <header className="thought-head">
      <TimeStamp at={e.at} />
      <IdToken id={e.id} />
      <span
        className="tag"
        title={e.is_autopilot ? 'decided by rule, no model' : 'decided with a model'}
      >
        {e.is_autopilot ? 'Autopilot' : 'Awake'}
      </span>
      <span className="tag" title={CLEARANCE_MEANINGS[e.clearance]}>
        {clearanceWords(e.clearance)}
      </span>
      <TaintTag episode={e} />
      <span className="tag" title={e.trigger_kind}>
        <span className="thought-label">woken by</span>
        {triggerWords(e.trigger_kind)}
      </span>
    </header>
  );
}

export function EpisodeCard(props: { episode: EpisodeView }): ReactElement {
  const e = props.episode;
  const hasUsage = !e.is_autopilot;
  return (
    <article
      className={e.is_autopilot ? 'thought comb-cell thought--autopilot' : 'thought comb-cell'}
    >
      <Head episode={e} />
      <p className="thought-trigger">{e.trigger_summary}</p>
      {e.reasoning_summary ? (
        <p className="thought-reasoning">
          <span className="thought-label">Reasoning</span> {e.reasoning_summary}
        </p>
      ) : null}
      <dl className="thought-pairs">
        <Pair label="Decision">{e.decision}</Pair>
        <Pair label="Action">
          <span title={e.action}>{plainCode(e.action)}</span>
        </Pair>
      </dl>
      {e.prompt_ref ? (
        <Section title="Prompt reference">
          <p>
            The prompt this episode ran on, by reference (the Landing Board does not serve its text
            yet): <code>{e.prompt_ref}</code>
          </p>
        </Section>
      ) : null}
      {e.trigger_ref ? (
        <Section title="Trigger detail">
          <p>
            What triggered it, by reference: <code>{e.trigger_ref}</code>
          </p>
        </Section>
      ) : null}
      {hasUsage ? (
        <Section title="Usage">
          <Usage episode={e} />
        </Section>
      ) : null}
    </article>
  );
}
