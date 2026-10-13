/*
 * The share sheet as an intake: any app on the phone can hand the Hive text, an image or audio.
 *
 * MainActivity declares share-target intent filters for `text/plain`, `image/*` and `audio/*`. When
 * one fires, this plugin reads the item and raises a `shared` event for JavaScript, which shows the
 * human a confirmation card before anything is sent. A share that arrives while the app is closed
 * is retained until JavaScript starts listening, so a cold start loses nothing.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Registered by MainActivity under the name
 *   "ShareIntake"; consumed by `src/landing_board/client/platform/android/share.ts`.
 *
 * Key invariants:
 *   - Nothing is posted from here. This plugin only reads the intent; the web side confirms and
 *     sends through the Landing Board with the device's own session.
 *   - A consumed share intent is replaced by a plain launch intent, so a rotation or a resume does
 *     not offer the same item again.
 *   - Other intents (the App Link that carries an invite, a launcher start) are left alone.
 *
 * See Also:
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md.
 *   - src/share/ for the confirmation card and the send.
 */
package dev.hivemind.observation.share

import android.content.Intent
import com.getcapacitor.Plugin
import com.getcapacitor.annotation.CapacitorPlugin

@CapacitorPlugin(name = "ShareIntake")
class ShareIntakePlugin : Plugin() {
    override fun load() {
        // The intent that started the app may itself be a share.
        take(activity.intent)
    }

    override fun handleOnNewIntent(intent: Intent) {
        super.handleOnNewIntent(intent)
        take(intent)
    }

    // Reads the item off a background thread (a photo is large) and emits it, retained until heard.
    private fun take(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val resolver = context.contentResolver
        // The read grant belongs to this intent, so it is read now and the intent is spent.
        activity.intent = Intent(Intent.ACTION_MAIN)
        bridge.executor.execute {
            SharedItem.from(intent, resolver)?.let { notifyListeners("shared", it, true) }
        }
    }
}
