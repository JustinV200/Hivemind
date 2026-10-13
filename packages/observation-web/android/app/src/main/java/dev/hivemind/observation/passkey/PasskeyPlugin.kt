/*
 * Passkeys through the Android Credential Manager, for the Observation Hive's Capacitor shell.
 *
 * The Landing Board enrols a phone with a passkey (`POST /v1/enrol/passkey-options`) and logs it in
 * with one (`passkey_options` in the login challenge). Both arrive as WebAuthn JSON and both are
 * answered with WebAuthn JSON. In a bundled WebView the page's own `navigator.credentials` cannot
 * make a credential bound to the Entrance's DNS name, so this plugin hands the same JSON to
 * `androidx.credentials` instead and returns the JSON the Entrance already accepts (ADR-0050).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Registered by MainActivity under the name "Passkey";
 *   called from `src/landing_board/client/platform/android/passkey.ts`, which is the Android member
 *   of the `platform` seam for `Browser.assertPasskey` and `Browser.createPasskey`.
 *
 * Key invariants:
 *   - The relying party is whatever `rpId` the Entrance put in the options (its DNS name), never
 *     the WebView's origin. The system checks the app against the Entrance's
 *     `/.well-known/assetlinks.json`; nothing here relaxes that.
 *   - User verification is whatever the options require. Nothing is stored, logged or cached: the
 *     answer goes back to JavaScript and nowhere else.
 *   - A failure is rejected with one of CANCELLED, NO_CREDENTIAL, UNSUPPORTED or FAILED, so the
 *     JavaScript side can treat a refused prompt as the client's one "passkey cancelled" outcome.
 *
 * See Also:
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md, "Passkeys through the Android
 *     Credential Manager".
 *   - src/landing_board/client/platform/android/passkey.ts for the TypeScript binding.
 */
package dev.hivemind.observation.passkey

import androidx.credentials.CreatePublicKeyCredentialRequest
import androidx.credentials.CreatePublicKeyCredentialResponse
import androidx.credentials.CredentialManager
import androidx.credentials.GetCredentialRequest
import androidx.credentials.GetPublicKeyCredentialOption
import androidx.credentials.PublicKeyCredential
import androidx.credentials.exceptions.CreateCredentialCancellationException
import androidx.credentials.exceptions.CreateCredentialProviderConfigurationException
import androidx.credentials.exceptions.CreateCredentialUnsupportedException
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialProviderConfigurationException
import androidx.credentials.exceptions.GetCredentialUnsupportedException
import androidx.credentials.exceptions.NoCredentialException
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

@CapacitorPlugin(name = "Passkey")
class PasskeyPlugin : Plugin() {
    // The Credential Manager shows system UI, so its calls start on the main thread.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    @PluginMethod
    fun create(call: PluginCall) {
        val options = call.getString("optionsJson") ?: return call.reject("optionsJson is required", FAILED)
        settle(call) { manager ->
            val answer = manager.createCredential(activity, CreatePublicKeyCredentialRequest(options))
            // Only a passkey registration is expected; anything else is a provider fault.
            (answer as? CreatePublicKeyCredentialResponse)?.registrationResponseJson
        }
    }

    @PluginMethod
    fun get(call: PluginCall) {
        val options = call.getString("optionsJson") ?: return call.reject("optionsJson is required", FAILED)
        settle(call) { manager ->
            val request = GetCredentialRequest(listOf(GetPublicKeyCredentialOption(options)))
            val answer = manager.getCredential(activity, request)
            (answer.credential as? PublicKeyCredential)?.authenticationResponseJson
        }
    }

    // Runs one ceremony, then resolves `{ json }` or rejects with a coarse code (never the cause).
    private fun settle(call: PluginCall, ceremony: suspend (CredentialManager) -> String?) {
        scope.launch {
            try {
                val json = ceremony(CredentialManager.create(activity))
                if (json == null) call.reject("The provider returned no passkey.", FAILED)
                else call.resolve(JSObject().put("json", json))
            } catch (error: Exception) {
                call.reject("The passkey prompt did not finish.", codeFor(error))
            }
        }
    }

    // Maps the Credential Manager's exception types to the four codes JavaScript understands.
    private fun codeFor(error: Exception): String = when (error) {
        is CreateCredentialCancellationException, is GetCredentialCancellationException -> CANCELLED
        is NoCredentialException -> NO_CREDENTIAL
        is CreateCredentialProviderConfigurationException,
        is GetCredentialProviderConfigurationException,
        is CreateCredentialUnsupportedException,
        is GetCredentialUnsupportedException -> UNSUPPORTED
        else -> FAILED
    }

    override fun handleOnDestroy() {
        // A ceremony still waiting when the activity goes must not resolve into a dead bridge.
        scope.cancel()
    }

    private companion object {
        const val CANCELLED = "CANCELLED"
        const val NO_CREDENTIAL = "NO_CREDENTIAL"
        const val UNSUPPORTED = "UNSUPPORTED"
        const val FAILED = "FAILED"
    }
}
