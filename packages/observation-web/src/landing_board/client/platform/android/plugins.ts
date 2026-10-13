/**
 * The shapes of the three native plugins, and the one place that binds them to Capacitor.
 *
 * The Android project (android/app/src/main/java/dev/hivemind/observation/) owns three plugins:
 * `Passkey` (Credential Manager), `UnifiedPush` (distributor registration) and `ShareIntake` (the
 * share sheet). Their JavaScript-visible surface is written down here as interfaces so every
 * adapter in this folder takes a plugin as an argument and is tested with a hand-made fake; only
 * `realPlugins` touches Capacitor.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Read by the other
 *   files in this folder; `realPlugins` is called once by `index.ts` when the app runs in the APK.
 *
 * Key invariants:
 *   - These interfaces match the Kotlin plugins' method names and result keys exactly; a change on
 *     either side is a change on both.
 *   - Plugin errors arrive as rejections whose `code` is one of the codes the Kotlin side lists.
 *   - Nothing here reads the network; the plugins hand over data and the client sends it.
 *
 * See Also:
 *   - android/app/src/main/java/dev/hivemind/observation/ for the Kotlin side.
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md.
 */
import { registerPlugin } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';

// WebAuthn JSON in, WebAuthn JSON out, as one string each way (the Entrance's own shapes).
export interface PasskeyPlugin {
  create(options: { optionsJson: string }): Promise<{ json: string }>;
  get(options: { optionsJson: string }): Promise<{ json: string }>;
}

// The endpoint and the Web Push keys (unpadded base64url) a distributor handed the app.
export interface PushRegistration {
  endpoint: string;
  p256dh: string;
  auth: string;
}

export interface UnifiedPushPlugin {
  register(options: { vapidKey: string }): Promise<PushRegistration>;
  unregister(): Promise<void>;
}

// What the Kotlin side emits for a share; `base64` is present for an image or a clip.
export interface SharedPayload {
  kind: 'text' | 'image' | 'audio' | 'refused';
  text?: string;
  base64?: string;
  mediaType?: string;
  reason?: string;
}

export interface ShareIntakePlugin {
  addListener(
    event: 'shared',
    listener: (payload: SharedPayload) => void,
  ): Promise<PluginListenerHandle>;
}

export interface NativePlugins {
  passkey: PasskeyPlugin;
  push: UnifiedPushPlugin;
  share: ShareIntakePlugin;
}

// Plugin names are those in each Kotlin class's `@CapacitorPlugin(name = ...)`.
export function realPlugins(): NativePlugins {
  return {
    passkey: registerPlugin<PasskeyPlugin>('Passkey'),
    push: registerPlugin<UnifiedPushPlugin>('UnifiedPush'),
    share: registerPlugin<ShareIntakePlugin>('ShareIntake'),
  };
}
