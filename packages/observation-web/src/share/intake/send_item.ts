/**
 * Send one confirmed shared item through the Hive's existing write paths.
 *
 * The share sheet is an intake, not a second write path (codingrules 8.11): text goes through the
 * Chat view's own `send`, a clip through its `speak` as a message, and an image through the image
 * intake route, which deposits it as Nectar labelled `C2`. Each runs under `guarded`, so a step-up
 * is asked for and retried once exactly as in the chat. Nothing is sent without a human tap: this
 * function is only called from the card's Send button.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Called by `share_card.tsx` with the chat's actions.
 *
 * Key invariants:
 *   - Audio and images are sent only under a format the Hive takes; any other type is refused
 *     before a request.
 *   - Text uses `actions.send`, which never throws (a failure shows as a failed message in the
 *     chat); audio and image throw, so the card shows the refusal in place.
 *   - The item is not stored anywhere by this function.
 *
 * See Also:
 *   - src/views/chat/writes/use_actions.ts for `send` and `speak`.
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md, "The share sheet is an intake".
 */
import type { HttpClient, SharedFile, SharedItem } from '../../landing_board/client';
import type { ChatActions, StepUp } from '../../views/chat';
import { shareFailure } from '../local_error';
import { audioTypeFor, imageTypeFor } from './media';

export interface SendDeps {
  actions: Pick<ChatActions, 'send' | 'speak'>;
  guarded: StepUp['guarded'];
  http: Pick<HttpClient, 'postImage'>;
  // The clip's length in seconds; the real one reads it from the WebView, a test supplies its own.
  measure: (clip: SharedFile) => Promise<number>;
}

export async function sendShared(item: SharedItem, deps: SendDeps): Promise<void> {
  switch (item.kind) {
    case 'text':
      await deps.actions.send(item.text);
      return;
    case 'audio': {
      const mediaType = audioTypeFor(item.mediaType);
      if (!mediaType) throw shareFailure('The Hive does not take that audio format.');
      const durationS = await deps.measure(item);
      await deps.actions.speak({ bytes: item.bytes, mediaType, durationS }, 'chat');
      return;
    }
    case 'image': {
      const mediaType = imageTypeFor(item.mediaType);
      if (!mediaType) throw shareFailure('The Hive takes JPEG, PNG and WebP images.');
      await deps.guarded(() => deps.http.postImage({ bytes: item.bytes, mediaType }));
      return;
    }
    case 'refused':
      throw shareFailure('That item could not be taken.');
  }
}
