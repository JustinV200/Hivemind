/**
 * The share sheet on Android: `Browser.shareIntake`, over the native `ShareIntake` plugin.
 *
 * Another app shares text, an image or an audio clip with the Hive and the Kotlin plugin raises a
 * `shared` event. A clip or image crosses the Capacitor bridge as base64, so this adapter decodes it
 * to bytes and hands the page one `SharedItem`. It does not send anything: the confirmation card in
 * `src/share/` shows the human what will go where, and only a tap sends it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Built by `index.ts`;
 *   consumed by `src/share/`.
 *
 * Key invariants:
 *   - A payload that is malformed (a file with no bytes, an unknown kind) is delivered as a
 *     `refused` item, never dropped silently and never half-decoded.
 *   - Listening is removable: the returned function stops delivery, even if the plugin's own
 *     listener handle has not arrived yet.
 *   - Items are not stored; one that arrived before anyone listened is retained by the plugin.
 *
 * See Also:
 *   - android/.../share/ShareIntakePlugin.kt for the native side and the event's shape.
 */
import type { PluginListenerHandle } from '@capacitor/core';
import type { Bytes, ShareIntake, SharedItem } from '../../browser';
import type { ShareIntakePlugin, SharedPayload } from './plugins';

function bytesOf(base64: string): Bytes {
  const binary = atob(base64);
  const bytes: Bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function decodeShared(payload: SharedPayload): SharedItem {
  if (payload.kind === 'text' && typeof payload.text === 'string') {
    return { kind: 'text', text: payload.text };
  }
  if (
    (payload.kind === 'image' || payload.kind === 'audio') &&
    typeof payload.base64 === 'string' &&
    typeof payload.mediaType === 'string'
  ) {
    const file = { bytes: bytesOf(payload.base64), mediaType: payload.mediaType };
    return payload.kind === 'image' ? { kind: 'image', ...file } : { kind: 'audio', ...file };
  }
  return { kind: 'refused', reason: payload.reason ?? 'unreadable' };
}

export function androidShareIntake(plugin: ShareIntakePlugin): ShareIntake {
  return {
    onShared(listener) {
      let handle: PluginListenerHandle | null = null;
      let stopped = false;
      void plugin
        .addListener('shared', (payload) => listener(decodeShared(payload)))
        .then((made) => {
          // A stop that came before the handle did must still end the subscription.
          if (stopped) void made.remove();
          else handle = made;
        });
      return () => {
        stopped = true;
        void handle?.remove();
        handle = null;
      };
    },
  };
}
