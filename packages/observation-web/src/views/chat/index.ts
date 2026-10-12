/**
 * The Chat view's face: the route component and the slice `src/app.tsx` registers.
 *
 * The view is a package (a slice, reads, writes, a conversation, a composer, push-to-talk and a
 * stylesheet), so importers take it from here and the layout inside can change without touching the
 * shell or the app. The route component is `ChatScreen`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/`. Imported by `src/shell/outlet.tsx` (the screen)
 *   and `src/app.tsx` (the slice).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/chat/README.md for the view's files.
 */
export { ChatScreen } from './chat_screen';
export { chatSlice } from './slice';
export type { ChatEvent, ChatState } from './slice';
// For the phone's share intake (src/share/), which sends through the chat's own writes.
export { useChatActions } from './writes/use_actions';
export type { ChatActions } from './writes/use_actions';
export { StepUpPrompt } from './writes/step_up_prompt';
export type { StepUp } from './writes/use_step_up';
