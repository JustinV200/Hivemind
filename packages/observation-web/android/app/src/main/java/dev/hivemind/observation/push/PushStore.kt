/*
 * What the push distributor last told this app, kept across process death, plus the hand-off
 * between the broadcast receiver and the plugin call that is waiting for it.
 *
 * UnifiedPush delivers its answers as broadcasts to a receiver, which may run while the app's
 * WebView does not exist. The endpoint and its Web Push keys therefore go to private preferences
 * the moment they arrive, and a plugin call that is waiting (the human just turned notifications
 * on) is completed through `PushRelay`. The endpoint and keys are public key material and a URL
 * the distributor chose; the app registers them with the Entrance and holds nothing secret here
 * beyond the auth secret that Web Push itself requires, which stays in app-private storage.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Written by PushReceiver, read and completed by
 *   UnifiedPushPlugin.
 *
 * Key invariants:
 *   - Preferences are MODE_PRIVATE and excluded from backup (see AndroidManifest).
 *   - At most one registration waits at a time; a new one replaces the old.
 *   - Nothing here reads a push message's content; there is none to read (ADR-0042).
 *
 * See Also:
 *   - src/landing_board/client/platform/android/push.ts for the TypeScript binding.
 */
package dev.hivemind.observation.push

import android.content.Context

data class StoredEndpoint(val endpoint: String, val p256dh: String, val auth: String)

object PushStore {
    private const val FILE = "hive_push"

    fun save(context: Context, stored: StoredEndpoint) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
            .putString("endpoint", stored.endpoint)
            .putString("p256dh", stored.p256dh)
            .putString("auth", stored.auth)
            .apply()
    }

    fun clear(context: Context) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().clear().apply()
    }
}

// The one waiting registration. Receivers run on the main thread, as do plugin calls.
object PushRelay {
    private var waiting: ((StoredEndpoint?, String?) -> Unit)? = null

    fun await(done: (StoredEndpoint?, String?) -> Unit) {
        waiting = done
    }

    // Completes the waiting call with an endpoint or a reason code, once.
    fun deliver(endpoint: StoredEndpoint?, failure: String?) {
        val done = waiting
        waiting = null
        done?.invoke(endpoint, failure)
    }
}
