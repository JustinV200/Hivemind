/**
 * `AskPanel`: the Help screen's Ask, a question box and the answers with their sources.
 *
 * The panel stands in place of a page's text at `#/help/ask` (design.md section 5.12). It opens
 * with one note, always shown, that an AI helper answers from these pages and nothing else,
 * lists the sections it read, can be wrong, and cannot see or change the Hive; then the box, the
 * Ask button, and a row of example questions a newcomer might have. Each exchange shows the
 * question, then `Asking the help` while the answer is out, then the words or, in a sentence,
 * why there are none (the outcome the Entrance names in a word), and the sources as links into
 * the pages, `(cited)` after the ones the answer rests on. A refusal is the `ErrorNotice`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/ask/`. Drawn by `help_screen.tsx`; the read is
 *   made by `use_ask.ts`.
 *
 * Key invariants:
 *   - Every request is the GET the hook makes; this component only calls it.
 *   - Colour is never the only carrier: pending, refused and links-only states are words, and a
 *     cited source says so in words.
 *   - The answer's words are drawn as text, never as markup.
 *
 * See Also:
 *   - src/views/help/ask/use_ask.ts and docs/observation/design.md section 5.12.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import { hrefFor } from '../../../router';
import { helpPage } from '../pages';
import { MAX_QUESTION_CHARS, MIN_QUESTION_CHARS, useAsk } from './use_ask';
import type { Exchange, HelpAnswer, HelpSource } from './use_ask';

// Questions a newcomer has before knowing the words to search for.
export const EXAMPLE_QUESTIONS: readonly string[] = [
  'What is a Warden?',
  'What do the Comb Shield levels mean?',
  'Why is a question waiting on me?',
  'How do I sort the Fleet table?',
];

// One sentence for each outcome without words, so the screen never shows a bare code.
const OUTCOME_WORDS: Readonly<Record<Exclude<HelpAnswer['outcome'], 'ANSWERED'>, string>> = {
  NO_MODEL: 'No model is configured on this Hive, so here are the sections that match instead.',
  MODEL_FAILED: 'The model did not answer in time. Here are the sections that match.',
  NOTHING_FOUND: 'Nothing in the help matches that question. Try other words, or the search box.',
};

// A source as a link into its page and section, named the way the page list names the page.
function SourceLink(props: { source: HelpSource }): ReactElement {
  const { source } = props;
  const title = helpPage(source.page)?.title ?? source.page;
  const href = hrefFor({ name: 'help', page: source.page, section: source.section });
  return (
    <li>
      <a href={href}>{source.heading ? `${title}: ${source.heading}` : title}</a>
      {source.cited ? <span className="help-ask-cited"> (cited)</span> : null}
    </li>
  );
}

function Answer(props: { answer: HelpAnswer }): ReactElement {
  const { answer } = props;
  return (
    <>
      <p className="help-ask-answer">
        {answer.outcome === 'ANSWERED' ? answer.answer : OUTCOME_WORDS[answer.outcome]}
      </p>
      {answer.sources.length > 0 ? (
        <div className="help-ask-sources">
          <p>Sources</p>
          <ol>
            {answer.sources.map((source) => (
              <SourceLink key={`${source.page}/${source.section ?? ''}`} source={source} />
            ))}
          </ol>
        </div>
      ) : null}
    </>
  );
}

function ExchangeRow(props: { exchange: Exchange }): ReactElement {
  const { exchange } = props;
  return (
    <li className="help-ask-exchange">
      <p className="help-ask-question">
        <span className="help-ask-label">You asked</span> {exchange.question}
      </p>
      {exchange.pending ? <p aria-busy="true">Asking the help</p> : null}
      {exchange.error ? <ErrorNotice error={exchange.error} /> : null}
      {exchange.answer ? <Answer answer={exchange.answer} /> : null}
    </li>
  );
}

// The box, its button and the example pills; the typed words are this form's own state, cleared
// once a question is sent, so the answer's exchange keeps them instead.
function QuestionForm(props: { asking: boolean; onAsk: (question: string) => void }): ReactElement {
  const [text, setText] = useState('');
  const send = (question: string): void => {
    props.onAsk(question);
    setText('');
  };
  const submit = (event: FormEvent): void => {
    event.preventDefault();
    send(text);
  };
  const tooShort = text.trim().length < MIN_QUESTION_CHARS;
  return (
    <>
      <form className="help-ask-form" role="search" onSubmit={submit}>
        <label htmlFor="help-ask-q">Your question</label>
        <input
          id="help-ask-q"
          type="text"
          value={text}
          maxLength={MAX_QUESTION_CHARS}
          placeholder="What is a Warden?"
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="button" disabled={props.asking || tooShort}>
          Ask
        </button>
      </form>
      <p className="help-ask-examples">
        <span className="help-ask-label">Try</span>
        {EXAMPLE_QUESTIONS.map((question) => (
          <button
            key={question}
            type="button"
            className="help-ask-example"
            disabled={props.asking}
            onClick={() => send(question)}
          >
            {question}
          </button>
        ))}
      </p>
    </>
  );
}

export function AskPanel(): ReactElement {
  const ask = useAsk();
  return (
    <section className="help-ask comb-cell" aria-labelledby="help-ask-title">
      <h2 id="help-ask-title" className="section-heading">
        Ask
      </h2>
      <p className="help-ask-note">
        An AI helper answers from these help pages and nothing else, and lists the sections it read
        so you can check them. It can be wrong, it cannot see your Hive, and it cannot change
        anything.
      </p>
      <QuestionForm asking={ask.asking} onAsk={ask.submit} />
      {ask.exchanges.length > 0 ? (
        <ol className="help-ask-log" aria-live="polite" aria-label="Answers">
          {ask.exchanges.map((exchange) => (
            <ExchangeRow key={exchange.id} exchange={exchange} />
          ))}
        </ol>
      ) : null}
    </section>
  );
}
