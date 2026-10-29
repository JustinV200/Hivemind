/**
 * The Chat reducer: folds a page, a stream frame or the result of an action into the slice.
 *
 * Lines are kept in log order (`seq`), each id once, cut to `MAX_LINES` from the old end. A line
 * that arrives settles the pending message with the same id, whether it came from the seed read,
 * the stream or even before the `POST` answered. A live question or Alarm line bumps `inboxWanted`
 * so the view reads the inbox once for it; the seed never does, because the seed reads the inbox
 * itself. Answering a question or acknowledging an Alarm removes it from the inbox at once.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/slice/`. Wrapped into a `Slice` by `index.ts`.
 *
 * Key invariants:
 *   - Pure: no clock, no randomness; times and keys come in with the event.
 *   - A repeated line (a stream frame the seed already held) changes nothing.
 *   - The reducer returns its input when an event changes nothing, so no view re-renders.
 *
 * See Also:
 *   - src/views/chat/slice/state.ts for the state and its bounds.
 *   - tests/views/chat/slice.test.ts for the rules above, one test each.
 */
import { MAX_GOALS, MAX_LINES, MAX_PENDING } from './state';
import type { ChatEvent, ChatLine, ChatState, GoalView, PendingMessage } from './state';

// `held` plus `incoming`, each id once and in `seq` order, cut to the bound at the old end.
function mergeLines(held: readonly ChatLine[], incoming: readonly ChatLine[]): ChatLine[] {
  const byId = new Map<string, ChatLine>();
  for (const line of [...held, ...incoming]) byId.set(line.id, line);
  const ordered = [...byId.values()].sort((a, b) => a.seq - b.seq);
  return ordered.slice(-MAX_LINES);
}

// Drops every pending message whose line is now in the conversation.
function settle(state: ChatState): ChatState {
  const ids = new Set(state.lines.map((line) => line.id));
  const pending = state.pending.filter((entry) => entry.lineId === null || !ids.has(entry.lineId));
  return pending.length === state.pending.length ? state : { ...state, pending };
}

function withLines(state: ChatState, lines: ChatLine[]): ChatState {
  return settle({ ...state, lines });
}

// A question or an Alarm from the Queen is something the human can act on: read the inbox for it.
const ACTIONABLE = new Set(['question', 'alarm']);

function framed(state: ChatState, entry: ChatLine): ChatState {
  // A frame the seed already held is a repeat; it must not ask for another inbox read.
  if (state.lines.some((line) => line.id === entry.id)) return state;
  const next = withLines(state, mergeLines(state.lines, [entry]));
  if (!ACTIONABLE.has(entry.kind)) return next;
  return { ...next, inboxWanted: state.inboxWanted + 1, announce: entry };
}

function withPending(state: ChatState, entry: PendingMessage): ChatState {
  const pending = [...state.pending.filter((held) => held.key !== entry.key), entry];
  return settle({ ...state, pending: pending.slice(-MAX_PENDING) });
}

function updatePending(state: ChatState, key: string, change: Partial<PendingMessage>): ChatState {
  const pending = state.pending.map((entry) =>
    entry.key === key ? { ...entry, ...change } : entry,
  );
  return settle({ ...state, pending });
}

function withGoal(state: ChatState, goal: GoalView): ChatState {
  const goals = [goal, ...state.goals.filter((held) => held.id !== goal.id)].slice(0, MAX_GOALS);
  return { ...state, goals };
}

// A typed goal request: its words become a line, and its progress joins the goals held.
function asked(state: ChatState, event: Extract<ChatEvent, { type: 'goal_asked' }>): ChatState {
  const { goal, text, at } = event;
  const entry = { id: goal.id, text, at };
  const rest = state.asked.filter((held) => held.id !== goal.id);
  return { ...withGoal(state, goal), asked: [...rest, entry].slice(-MAX_GOALS) };
}

function seeded(state: ChatState, entries: readonly ChatLine[]): ChatState {
  return { ...withLines(state, mergeLines(state.lines, entries)), seeded: true, error: null };
}

function spoken(state: ChatState, event: Extract<ChatEvent, { type: 'spoken' }>): ChatState {
  const { result, at } = event;
  let next: ChatState = { ...state, spoken: { result, at } };
  if (result.goal) next = withGoal(next, result.goal);
  // A spoken answer is this device's own, in the words the Entrance heard.
  if (result.answered)
    next = reduceChat(next, {
      type: 'answered',
      questionId: result.answered.question_id,
      text: result.transcript,
    });
  if (result.chat) {
    // A spoken chat line is a sent message: its transcript shows until its line arrives.
    const entry = {
      key: `voice:${result.chat.id}`,
      text: result.transcript,
      at,
      status: 'sent' as const,
    };
    next = withPending(next, { ...entry, lineId: result.chat.id, error: null });
  }
  return next;
}

// What this device itself said is kept (bounded like the pending list), so the settled line can
// show it; an answer given elsewhere carries no text and leaves the record alone.
function answered(state: ChatState, event: Extract<ChatEvent, { type: 'answered' }>): ChatState {
  if (event.text === undefined) return state;
  const rest = state.answered.filter((held) => held.id !== event.questionId);
  const entry = { id: event.questionId, text: event.text };
  return { ...state, answered: [...rest, entry].slice(-MAX_PENDING) };
}

function acknowledged(
  state: ChatState,
  event: Extract<ChatEvent, { type: 'acknowledged' }>,
): ChatState {
  if (!event.byYou || state.acknowledged.includes(event.alarmId)) return state;
  return { ...state, acknowledged: [...state.acknowledged, event.alarmId].slice(-MAX_PENDING) };
}

function withoutInboxItem(state: ChatState, kind: 'questions' | 'alarms', id: string): ChatState {
  if (!state.inbox || !state.inbox[kind].some((item) => item.id === id)) return state;
  const kept = state.inbox[kind].filter((item) => item.id !== id);
  return { ...state, inbox: { ...state.inbox, [kind]: kept } };
}

export function reduceChat(state: ChatState, event: ChatEvent): ChatState {
  switch (event.type) {
    case 'seeded':
      return seeded(state, event.page.entries);
    case 'failed':
      return { ...state, error: event.error, seeded: true };
    case 'inbox':
      return { ...state, inbox: event.inbox, inboxError: null };
    case 'inbox_failed':
      return { ...state, inboxError: event.error };
    case 'frame':
      // A frame without a line (a reply to push-to-talk, never sent to this socket) is ignored.
      return event.frame.type === 'chat' && event.frame.entry
        ? framed(state, event.frame.entry)
        : state;
    case 'compose':
      return withPending(state, {
        key: event.key,
        text: event.text,
        at: event.at,
        status: 'sending',
        lineId: null,
        error: null,
      });
    case 'sent':
      return updatePending(state, event.key, { status: 'sent', lineId: event.id });
    case 'send_failed':
      return updatePending(state, event.key, { status: 'failed', error: event.error });
    case 'dismiss_pending':
      return { ...state, pending: state.pending.filter((entry) => entry.key !== event.key) };
    case 'answered':
      return answered(withoutInboxItem(state, 'questions', event.questionId), event);
    case 'acknowledged':
      return acknowledged(withoutInboxItem(state, 'alarms', event.alarmId), event);
    case 'goal':
      return withGoal(state, event.goal);
    case 'goal_asked':
      return asked(state, event);
    case 'spoken':
      return spoken(state, event);
    case 'dismiss_spoken':
      return state.spoken ? { ...state, spoken: null } : state;
    case 'notice':
      return { ...state, notice: event.text };
    case 'dismiss_notice':
      return state.notice === null ? state : { ...state, notice: null };
  }
}
