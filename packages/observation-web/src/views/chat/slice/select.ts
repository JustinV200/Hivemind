/**
 * Selectors over the Chat slice: which inbox item a line stands for, and what is still waiting.
 *
 * A question or Alarm line carries the id to answer or acknowledge in `ref`; the inbox carries the
 * item itself (its options, its severity). These pure functions join the two, say what a settled
 * line should read (the answer this device gave, kept in the slice), find the message a reply
 * answers, and find the inbox items no line points at, which the view still shows so a waiting
 * question is never hidden just because its line has not arrived.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/slice/`. Called by the conversation components and
 *   the push-to-talk hook; reads the slice and changes nothing.
 *
 * Key invariants:
 *   - Pure and total: an unknown ref, or an inbox not yet read, answers null or an empty list.
 *   - "Resolved" is claimed only once the inbox has been read, never from silence.
 *
 * See Also:
 *   - src/views/chat/slice/state.ts for the state these read.
 */
import type { AlarmView, ChatLine, ChatState, GoalView, QuestionView } from './state';

export function questionFor(state: ChatState, line: ChatLine): QuestionView | null {
  if (line.kind !== 'question' || line.ref === null) return null;
  return state.inbox?.questions.find((question) => question.id === line.ref) ?? null;
}

export function alarmFor(state: ChatState, line: ChatLine): AlarmView | null {
  if (line.kind !== 'alarm' || line.ref === null) return null;
  return state.inbox?.alarms.find((alarm) => alarm.id === line.ref) ?? null;
}

// True once the inbox has been read and no longer holds the line's question or Alarm.
export function isResolved(state: ChatState, line: ChatLine): boolean {
  if (state.inbox === null || line.ref === null) return false;
  if (line.kind === 'question') return questionFor(state, line) === null;
  if (line.kind === 'alarm') return alarmFor(state, line) === null;
  return false;
}

// What a settled line says: the answer this device gave, its acknowledgement, or only that the
// item is no longer waiting (settled elsewhere); null while the line still waits.
export function resolvedWords(state: ChatState, line: ChatLine): string | null {
  if (!isResolved(state, line)) return null;
  const answer = state.answered.find((held) => held.id === line.ref);
  if (answer) return `You answered: ${answer.text}`;
  if (line.ref !== null && state.acknowledged.includes(line.ref)) return 'Acknowledged by you.';
  return 'No longer waiting.';
}

// The words of the line a reply answers, for the reply's links to carry as their `title`.
export function originOf(state: ChatState, line: ChatLine): string | null {
  if (line.kind !== 'reply' || line.ref === null) return null;
  return state.lines.find((held) => held.id === line.ref)?.text ?? null;
}

export interface Unmatched {
  questions: readonly QuestionView[];
  alarms: readonly AlarmView[];
}

// The waiting items whose chat line is not in the conversation (yet).
export function unmatchedInbox(state: ChatState): Unmatched {
  const refs = new Set(state.lines.map((line) => line.ref));
  return {
    questions: (state.inbox?.questions ?? []).filter((question) => !refs.has(question.id)),
    alarms: (state.inbox?.alarms ?? []).filter((alarm) => !refs.has(alarm.id)),
  };
}

// The latest known state of a goal request this device spoke.
export function goalById(state: ChatState, id: string): GoalView | null {
  return state.goals.find((goal) => goal.id === id) ?? null;
}
