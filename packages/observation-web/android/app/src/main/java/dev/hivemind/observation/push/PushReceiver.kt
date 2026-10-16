/*
 * The UnifiedPush receiver: what the distributor sends to this app, even while it is closed.
 *
 * A distributor (the operator's own ntfy on the VPN, or any distributor app) answers a
 * registration with an endpoint and the user agent's Web Push keys, and later forwards each push
 * the Entrance sends to that endpoint. The Entrance's `WebPush` channel encrypts every notice per
 * RFC 8291, so the connector library decrypts it for us and we never look at the result. A
 * distributor may also send a new endpoint nobody asked for (it moved, or re-registered after an
 * update) or say the app is unregistered; both are announced to JavaScript, which registers the
 * new endpoint with the Entrance or withdraws the dead one, so push never dies silently.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Declared in AndroidManifest.xml for the four
 *   `org.unifiedpush.android.connector.*` actions; hands answers to PushStore and PushRelay and
 *   raises the notice through WaitingNotice.
 *
 * Key invariants:
 *   - `onMessage` ignores the message completely: a push means "something is waiting", nothing else.
 *   - An endpoint without Web Push keys is refused (`UNENCRYPTED`): the Entrance never sends an
 *     unencrypted notice, so such a distributor cannot work (ADR-0050).
 *   - Keys are passed on as unpadded base64url, the form `POST /v1/push/subscriptions` takes.
 *   - An endpoint a plugin call waits for completes that call and nothing else; any other is a
 *     rotation and is announced. The distributor's answer to this app's own `unregister` is not.
 *
 * See Also:
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md, "Push is UnifiedPush, not FCM".
 *   - docs/adr/0042 for the content-free, padded notice.
 */
package dev.hivemind.observation.push

import android.content.Context
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.MessagingReceiver
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

class PushReceiver : MessagingReceiver() {
    override fun onNewEndpoint(context: Context, endpoint: PushEndpoint, instance: String) {
        val keys = endpoint.pubKeySet
        if (keys == null) {
            PushRelay.deliver(null, "UNENCRYPTED")
            return
        }
        val stored = StoredEndpoint(endpoint.url, base64Url(keys.pubKey), base64Url(keys.auth))
        PushStore.save(context, stored)
        // A registration is in hand again, so a stale "own unregister" mark no longer applies.
        PushRelay.takeOwnUnregister()
        // The call that asked for this endpoint takes it; an endpoint nobody asked for (or one
        // arriving after that call timed out) is a rotation JavaScript must register afresh.
        if (!PushRelay.deliver(stored, null)) PushRelay.announce(context, PushChange.ENDPOINT, stored)
    }

    override fun onMessage(context: Context, message: PushMessage, instance: String) {
        // The content is never read: the notice is content-free by design.
        WaitingNotice.post(context)
    }

    override fun onRegistrationFailed(context: Context, reason: FailedReason, instance: String) {
        PushRelay.deliver(null, "REGISTRATION_FAILED")
    }

    override fun onUnregistered(context: Context, instance: String) {
        PushStore.clear(context)
        // The answer to this app's own `unregister` is expected; anything else means the
        // distributor dropped the app, and the Entrance's subscription is now a dead letter.
        if (!PushRelay.takeOwnUnregister()) PushRelay.announce(context, PushChange.UNREGISTERED, null)
    }

    // Normalises standard base64 to the unpadded URL alphabet, whichever the distributor used.
    private fun base64Url(text: String): String =
        text.replace('+', '-').replace('/', '_').trimEnd('=')
}
