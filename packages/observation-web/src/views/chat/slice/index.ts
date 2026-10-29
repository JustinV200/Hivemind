/**
 * The Chat slice's face: the `Slice` the app registers, its types and its selectors.
 *
 * The slice is a package (state and events, the reducer, the selectors), so importers take it from
 * here and the layout inside can change. Importing this module also merges the slice into the
 * store's registry, which is what types `useSlice('chat')` and `dispatch`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/slice/`. Imported by the view's hooks and
 *   components, and by `src/app.tsx` through `src/views/chat/index.ts`.
 *
 * Key invariants:
 *   - No logic beyond building the `Slice` value and the registry merge.
 *
 * See Also:
 *   - src/store/registry.ts for how a view's slice joins the table.
 */
import type { Slice } from '../../../store';
import { reduceChat } from './reduce';
import { initialChat } from './state';
import type { ChatEvent, ChatState } from './state';

export const chatSlice: Slice<ChatState, ChatEvent> = { initial: initialChat, reduce: reduceChat };

export { reduceChat } from './reduce';
export {
  alarmFor,
  goalById,
  isResolved,
  originOf,
  questionFor,
  resolvedWords,
  unmatchedInbox,
} from './select';
export { initialChat, MAX_GOALS, MAX_LINES, MAX_PENDING } from './state';
export type {
  AlarmView,
  AskedGoal,
  ChatEvent,
  ChatFrame,
  ChatLine,
  ChatPage,
  ChatState,
  GoalView,
  InboxView,
  PendingMessage,
  QuestionView,
  SpokenState,
  VoiceAccepted,
} from './state';

// Tells the store's hooks about this slice, so `useSlice('chat')` and `dispatch` are typed.
declare module '../../../store/registry' {
  interface SliceRegistry {
    chat: Slice<ChatState, ChatEvent>;
  }
}
