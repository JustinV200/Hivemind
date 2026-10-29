/**
 * `useAsk()`: send a question to the help and keep the exchanges the panel shows.
 *
 * One question at a time goes to `GET /v1/help/ask?q=`, the Help screen's one read (ADR-0069).
 * Each question becomes an exchange the moment it is sent, pending until the answer or the
 * refusal lands, so the panel can show the question, then `Asking the help`, then the words.
 * Nothing here writes to the Hive, and nothing is kept past the screen: the exchanges live in
 * component state and go when the panel unmounts.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/ask/`. Used by `ask_panel.tsx`; reads through the
 *   Landing Board client from the store's provider.
 *
 * Key invariants:
 *   - One GET per question; a question is never repeated on its own, and a second one waits
 *     for the first (`asking`).
 *   - An exchange keeps its place: an answer updates the exchange it belongs to by id.
 *
 * See Also:
 *   - src/views/help/ask/ask_panel.tsx for what is drawn.
 */
import { useCallback, useRef, useState } from 'react';
import type { components } from '../../../landing_board/types';
import { useHive } from '../../../store';

export type HelpAnswer = components['schemas']['HelpAnswerView'];
export type HelpSource = components['schemas']['HelpSourceView'];

// The route refuses a question shorter or longer than this.
export const MIN_QUESTION_CHARS = 3;
export const MAX_QUESTION_CHARS = 500;

export interface Exchange {
  id: number;
  question: string;
  // The answer once it lands; null while pending or after a refusal.
  answer: HelpAnswer | null;
  // The refusal, when the Hive gave one; null otherwise.
  error: unknown;
  pending: boolean;
}

export interface Ask {
  exchanges: readonly Exchange[];
  // True while a question is out, so the box waits for its answer.
  asking: boolean;
  submit: (question: string) => void;
}

export function useAsk(): Ask {
  const { client } = useHive();
  const [exchanges, setExchanges] = useState<readonly Exchange[]>([]);
  const [asking, setAsking] = useState(false);
  const nextId = useRef(1);

  const submit = useCallback(
    (question: string) => {
      const words = question.trim();
      if (words.length < MIN_QUESTION_CHARS || asking) return;
      const id = nextId.current++;
      setExchanges((prev) => [
        ...prev,
        { id, question: words, answer: null, error: null, pending: true },
      ]);
      setAsking(true);
      // The exchange is found by id when the answer lands, so a later question never takes it.
      const settle = (patch: Partial<Exchange>): void =>
        setExchanges((prev) =>
          prev.map((exchange) =>
            exchange.id === id ? { ...exchange, ...patch, pending: false } : exchange,
          ),
        );
      client.http
        .call('get', '/v1/help/ask', { query: { q: words } })
        .then((answer) => settle({ answer }))
        .catch((error: unknown) => settle({ error }))
        .finally(() => setAsking(false));
    },
    [client, asking],
  );

  return { exchanges, asking, submit };
}
