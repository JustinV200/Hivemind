/*
 * UnifiedPush registration for the Observation Hive: ask a distributor for a Web Push endpoint.
 *
 * Push on a phone with the app closed needs a distributor the operator controls (ADR-0047). This
 * plugin picks the saved or only distributor, registers under the Hive's VAPID application key
 * and resolves with the endpoint plus `p256dh` and `auth` keys, which JavaScript registers with
 * `POST /v1/push/subscriptions` as `channel: web_push`. The Entrance's existing channel delivers
 * to it unchanged. No Firebase, no Google Play services and no third party the operator did not
 * choose sit on the path.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Registered by MainActivity under the name
 *   "UnifiedPush"; called from `src/landing_board/client/platform/android/push.ts`
 *   (`Browser.subscribeWebPush` and `Browser.unsubscribeWebPush`). The receiving side is
 *   PushReceiver.
 *
 * Key invariants:
 *   - Registration is rejected with NO_DISTRIBUTOR (none installed or chosen), DENIED (notification
 *     permission refused), UNENCRYPTED (the distributor offered no Web Push keys),
 *     REGISTRATION_FAILED or TIMEOUT; JavaScript turns each into a sentence for the human.
 *   - Notification permission is requested here (Android 13+) because a push the human cannot see
 *     is worthless; nothing is registered when it is refused.
 *   - `unregister` forgets the endpoint locally and tells the distributor; the Entrance's copy is
 *     deleted by JavaScript through the signed route.
 *
 * See Also:
 *   - docs/adr/0047-android-client-capacitor-and-push-delivery.md.
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
                else call.resolve(
                    JSObject()
                        .put("endpoint", endpoint.endpoint)
                        .put("p256dh", endpoint.p256dh)
                        .put("auth", endpoint.auth),
                )
            }
            main.postDelayed(giveUp, WAIT_MS)
            UnifiedPush.register(context, INSTANCE, null, vapid)
        }
    }

    @PluginMethod
    fun unregister(call: PluginCall) {
        UnifiedPush.unregister(context, INSTANCE)
        PushStore.clear(context)
        call.resolve()
    }

    private companion object {
        const val INSTANCE = "default"
        // A distributor app may need a moment to start; beyond this the human is told to retry.
        const val WAIT_MS = 20_000L
    }
}
