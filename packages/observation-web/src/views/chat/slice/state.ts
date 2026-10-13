/**
 * The Chat slice's shape: its state, its events and the bounds that keep it small.
 *
 * The slice holds everything the chat needs to draw: the conversation lines in log order, the
 * messages the human has sent that have not come back on the stream yet, the inbox (the questions
 * and Alarms still waiting on the human), the goals this device spoke and may need to confirm, the
 * goals it typed and asked for as goals (their words, with the request's progress in `goals`), and
 * the last spoken result. Every member is a generated Landing Board type as received; nothing is
 * reshaped, so the view renders what the schema carries and no more.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/slice/`. Reduced by `reduce.ts`, merged into the
 *   store's registry by `index.ts`, filled by `reads/` (reads and frames) and `writes/` (the
 *   results of the human's own actions).
 *
 * Key invariants:
 *   - Every list is bounded here: `MAX_LINES`, `MAX_PENDING` and `MAX_GOALS` (which also bounds
 *     the goals asked for).
 *   - Events carry their own times and keys, so the reducer stays pure.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6.
 *   - src/views/chat/slice/reduce.ts for how each event folds.
 */
import type { components } from '../../../landing_board/types';

type Schemas = components['schemas'];
export type ChatLine = Schemas['ChatLine'];
export type ChatPage = Schemas['ChatPage'];
export type ChatFrame = Schemas['ChatFrame'];
export type InboxView = Schemas['InboxView'];
export type QuestionView = Schemas['QuestionView'];
export type AlarmView = Schemas['AlarmView'];
export type GoalView = Schemas['GoalView'];
export type VoiceAccepted = Schemas['VoiceAccepted'];

// The newest lines kept; older ones fall off the front, as the stream only ever adds at the end.
export const MAX_LINES = 500;
// Sent-but-unsettled messages and failed ones the human has not dismissed.
export const MAX_PENDING = 20;
// Goals this device spoke; a handful is all one sitting produces.
export const MAX_GOALS = 20;

// What a message the human sent is doing: on its way, accepted (waiting for its line), or refused.
export type PendingStatus = 'sending' | 'sent' | 'failed';

export interface PendingMessage {
  // A key made on this device, so the entry exists before the Entrance has given the line an id.
  key: string;
  text: string;
  at: string;
  status: PendingStatus;
  // The id `POST /v1/chat` answered with; the line that settles this entry carries the same id.
  lineId: string | null;
  error: unknown;
}

// A goal the human typed and asked for as a goal: the words they used, shown as a line. The
// request's progress is the `GoalView` with the same id in `goals`.
export interface AskedGoal {
  id: string;
  text: string;
  at: string;
}

// The last clip the human spoke: what was heard and what the Entrance did with it.
export interface SpokenState {
  result: VoiceAccepted;
  at: string;
}

export interface ChatState {
  seeded: boolean;
  lines: readonly ChatLine[];
  pending: readonly PendingMessage[];
  // Null until the first inbox read answers.
  inbox: InboxView | null;
  inboxError: unknown;
  // A refused or failed chat read; a 403 here hides the composer.
  error: unknown;
  // Counts the question and Alarm lines that arrived live; each one wants the inbox read again.
  inboxWanted: number;
  // The newest live question or Alarm line, for the polite live region.
  announce: ChatLine | null;
  goals: readonly GoalView[];
  asked: readonly AskedGoal[];
  spoken: SpokenState | null;
  // One sentence about something that changed under the human, such as an answer given elsewhere.
  notice: string | null;
}

export type ChatEvent =
  | { type: 'seeded'; page: ChatPage }
  | { type: 'failed'; error: unknown }
  | { type: 'inbox'; inbox: InboxView }
  | { type: 'inbox_failed'; error: unknown }
  | { type: 'frame'; frame: ChatFrame }
  | { type: 'compose'; key: string; text: string; at: string }
  | { type: 'sent'; key: string; id: string }
  | { type: 'send_failed'; key: string; error: unknown }
  | { type: 'dismiss_pending'; key: string }
  | { type: 'answered'; questionId: string }
  | { type: 'acknowledged'; alarmId: string }
  | { type: 'goal'; goal: GoalView }
  | { type: 'goal_asked'; goal: GoalView; text: string; at: string }
  | { type: 'spoken'; result: VoiceAccepted; at: string }
  | { type: 'dismiss_spoken' }
  | { type: 'notice'; text: string }
  | { type: 'dismiss_notice' };

export const initialChat: ChatState = {
  seeded: false,
  lines: [],
  pending: [],
  inbox: null,
  inboxError: null,
  error: null,
  inboxWanted: 0,
  announce: null,
  goals: [],
  asked: [],
  spoken: null,
  notice: null,
};
