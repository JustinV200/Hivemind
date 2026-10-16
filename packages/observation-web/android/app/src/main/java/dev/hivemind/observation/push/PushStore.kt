/*
 * What the push distributor last told this app, kept across process death, plus the hand-off
 * between the broadcast receiver and the plugin call that is waiting for it.
 *
 * UnifiedPush delivers its answers as broadcasts to a receiver, which may run while the app's
 * WebView does not exist. The endpoint and its Web Push keys therefore go to private preferences
 * the moment they arrive, and a plugin call that is waiting (the human just turned notifications
 * on) is completed through `PushRelay`. An answer nobody is waiting for (the distributor rotated
 * the endpoint on its own, or dropped the app) is news JavaScript must act on: `PushRelay`
 * announces it through the loaded plugin, or, with no WebView up, leaves it pending here for the
 * plugin to announce when it next loads. The endpoint and keys are public key material and a URL
 * the distributor chose; the app registers them with the Entrance and holds nothing secret here
 * beyond the auth secret that Web Push itself requires, which stays in app-private storage.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Written by PushReceiver, read, completed and announced
 *   by UnifiedPushPlugin.
 *
 * Key invariants:
 *   - Preferences are MODE_PRIVATE and excluded from backup (see AndroidManifest).
 *   - At most one registration waits at a time; a new one replaces the old.
 *   - At most one change is pending at a time, the newest; it is taken once, at the next load.
 *   - Nothing here reads a push message's content; there is none to read (ADR-0042).
 *
 * See Also:
 *   - src/landing_board/client/platform/android/push.ts for the TypeScript binding.
 */
package dev.hivemind.observation.push

import android.content.Context

data class StoredEndpoint(val endpoint: String, val p256dh: String, val auth: String)

// A change the distributor made on its own, named as the plugin event that carries it.
enum class PushChange(val event: String) {
    ENDPOINT("endpoint"),
    UNREGISTERED("unregistered"),
}

object PushStore {
    private const val FILE = "hive_push"
    private const val PENDING = "pending"

    private fun prefs(context: Context) = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun save(context: Context, stored: StoredEndpoint) {
        prefs(context).edit()
            .putString("endpoint", stored.endpoint)
            .putString("p256dh", stored.p256dh)
            .putString("auth", stored.auth)
            .apply()
    }

    fun load(context: Context): StoredEndpoint? {
        val read = prefs(context)
        val endpoint = read.getString("endpoint", null) ?: return null
        val p256dh = read.getString("p256dh", null) ?: return null
        val auth = read.getString("auth", null) ?: return null
        return StoredEndpoint(endpoint, p256dh, auth)
    }

    // Forgets the endpoint and keys; a pending change set afterwards is kept.
    fun clear(context: Context) {
        prefs(context).edit().clear().apply()
    }

    // Remembers a change that arrived while no WebView was up, for the next plugin load.
    fun markPending(context: Context, change: PushChange) {
        prefs(context).edit().putString(PENDING, change.event).apply()
    }

    // Reads and forgets the pending change, so it is announced once.
    fun takePending(context: Context): PushChange? {
        val read = prefs(context)
        val event = read.getString(PENDING, null) ?: return null
        read.edit().remove(PENDING).apply()
        return PushChange.values().firstOrNull { it.event == event }
    }
}

// The one waiting registration, and the loaded plugin a change nobody waits for is announced
// through. Receivers run on the main thread, as do plugin calls and the plugin's load.
object PushRelay {
    private var waiting: ((StoredEndpoint?, String?) -> Unit)? = null
    private var plugin: UnifiedPushPlugin? = null
    // Set while this app's own `unregister` is out, so the distributor's answer is not announced
    // as the distributor dropping the app; a new endpoint resets it in case no answer ever came.
    private var ownUnregister = false

    fun attach(loaded: UnifiedPushPlugin) {
        plugin = loaded
    }

    fun detach(gone: UnifiedPushPlugin) {
        if (plugin === gone) plugin = null
    }

    fun await(done: (StoredEndpoint?, String?) -> Unit) {
        waiting = done
    }

    // Completes the waiting call with an endpoint or a reason code, once; false when none waited.
    fun deliver(endpoint: StoredEndpoint?, failure: String?): Boolean {
        val done = waiting ?: return false
        waiting = null
        done(endpoint, failure)
        return true
    }

    fun expectOwnUnregister() {
        ownUnregister = true
    }

    // True once, for the distributor's answer to this app's own `unregister`.
    fun takeOwnUnregister(): Boolean {
        val own = ownUnregister
        ownUnregister = false
        return own
    }

    // Tells JavaScript now, through the loaded plugin, or at the plugin's next load.
    fun announce(context: Context, change: PushChange, endpoint: StoredEndpoint?) {
        val loaded = plugin
        if (loaded == null) PushStore.markPending(context, change) else loaded.announce(change, endpoint)
    }
}
