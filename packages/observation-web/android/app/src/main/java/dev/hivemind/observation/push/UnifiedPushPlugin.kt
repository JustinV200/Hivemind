/*
 * UnifiedPush registration for the Observation Hive: ask a distributor for a Web Push endpoint.
 *
 * Push on a phone with the app closed needs a distributor the operator controls (ADR-0050). This
 * plugin picks the saved or only distributor, registers under the Hive's VAPID application key
 * and resolves with the endpoint plus `p256dh` and `auth` keys, which JavaScript registers with
 * `POST /v1/push/subscriptions` as `channel: web_push`. The Entrance's existing channel delivers
 * to it unchanged. No Firebase, no Google Play services and no third party the operator did not
 * choose sit on the path. What the distributor does on its own later reaches JavaScript as two
 * events: `endpoint` (a rotated registration, in the same shape `register` resolves with) and
 * `unregistered` (the distributor dropped the app). Both are retained until a listener is added,
 * and one that arrived while no WebView was up is raised when the plugin next loads.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Registered by MainActivity under the name
 *   "UnifiedPush"; called from `src/landing_board/client/platform/android/push.ts`
 *   (`Browser.subscribeWebPush`, `Browser.unsubscribeWebPush` and `Browser.pushChanges`). The
 *   receiving side is PushReceiver, which announces through PushRelay.
 *
 * Key invariants:
 *   - Registration is rejected with NO_DISTRIBUTOR (none installed or chosen), DENIED (notification
 *     permission refused), UNENCRYPTED (the distributor offered no Web Push keys),
 *     REGISTRATION_FAILED or TIMEOUT; JavaScript turns each into a sentence for the human.
 *   - Notification permission is requested here (Android 13+) because a push the human cannot see
 *     is worthless; nothing is registered when it is refused.
 *   - `unregister` forgets the endpoint locally and tells the distributor; the Entrance's copy is
 *     deleted by JavaScript through the signed route. The distributor's answer to it is not
 *     raised as an `unregistered` event.
 *   - The event names and their payload keys are those `plugins.ts` declares; a change on either
 *     side is a change on both.
 *
 * See Also:
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md.
 *   - docs/entrance/landing-board.md section 8.2 and 9.
 */
package dev.hivemind.observation.push

import android.Manifest
import android.os.Build
import android.os.Handler
import android.os.Looper
import com.getcapacitor.JSObject
import com.getcapacitor.PermissionState
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.getcapacitor.annotation.Permission
import com.getcapacitor.annotation.PermissionCallback
import org.unifiedpush.android.connector.UnifiedPush

@CapacitorPlugin(
    name = "UnifiedPush",
    permissions = [Permission(strings = [Manifest.permission.POST_NOTIFICATIONS], alias = "notifications")],
)
class UnifiedPushPlugin : Plugin() {
    private val main = Handler(Looper.getMainLooper())

    override fun load() {
        PushRelay.attach(this)
        // A change the receiver saw while no WebView was up is raised now, retained until heard.
        when (PushStore.takePending(context)) {
            PushChange.ENDPOINT -> PushStore.load(context)?.let { announce(PushChange.ENDPOINT, it) }
            PushChange.UNREGISTERED -> announce(PushChange.UNREGISTERED, null)
            null -> Unit
        }
    }

    override fun handleOnDestroy() {
        PushRelay.detach(this)
        super.handleOnDestroy()
    }

    // Raises one change for JavaScript; retained so a listener added later still hears it.
    internal fun announce(change: PushChange, endpoint: StoredEndpoint?) {
        val data = if (endpoint == null) JSObject() else registrationOf(endpoint)
        notifyListeners(change.event, data, true)
    }

    @PluginMethod
    fun register(call: PluginCall) {
        val needsAsk = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            getPermissionState("notifications") != PermissionState.GRANTED
        if (needsAsk) requestPermissionForAlias("notifications", call, "afterPermission")
        else begin(call)
    }

    @PermissionCallback
    private fun afterPermission(call: PluginCall) {
        if (getPermissionState("notifications") == PermissionState.GRANTED) begin(call)
        else call.reject("Notification permission was not granted.", "DENIED")
    }

    // Chooses the distributor, asks it for an endpoint under the VAPID key and waits for the answer.
    private fun begin(call: PluginCall) {
        val vapid = call.getString("vapidKey")
        UnifiedPush.tryUseCurrentOrDefaultDistributor(context) { chosen ->
            if (!chosen) {
                call.reject("No UnifiedPush distributor is installed or chosen.", "NO_DISTRIBUTOR")
                return@tryUseCurrentOrDefaultDistributor
            }
            val giveUp = Runnable { PushRelay.deliver(null, "TIMEOUT") }
            PushRelay.await { endpoint, failure ->
                main.removeCallbacks(giveUp)
                if (endpoint == null) call.reject("The distributor gave no endpoint.", failure ?: "REGISTRATION_FAILED")
                else call.resolve(registrationOf(endpoint))
            }
            main.postDelayed(giveUp, WAIT_MS)
            UnifiedPush.register(context, INSTANCE, null, vapid)
        }
    }

    @PluginMethod
    fun unregister(call: PluginCall) {
        // The distributor will answer with UNREGISTERED; that answer is ours, not a drop.
        PushRelay.expectOwnUnregister()
        UnifiedPush.unregister(context, INSTANCE)
        PushStore.clear(context)
        call.resolve()
    }

    // The shape `register` resolves with and the `endpoint` event carries: what `plugins.ts` reads.
    private fun registrationOf(endpoint: StoredEndpoint): JSObject =
        JSObject()
            .put("endpoint", endpoint.endpoint)
            .put("p256dh", endpoint.p256dh)
            .put("auth", endpoint.auth)

    private companion object {
        const val INSTANCE = "default"
        // A distributor app may need a moment to start; beyond this the human is told to retry.
        const val WAIT_MS = 20_000L
    }
}
