/*
 * Turn an Android share intent into the one small value JavaScript receives.
 *
 * Another app (a glasses companion app included) shares text, an image or an audio clip with the
 * Hive. This file reads that intent and produces `{ kind, text }` or `{ kind, base64, mediaType }`,
 * or `{ kind: "refused", reason }` for something the Hive does not take. It reads the bytes itself,
 * at once, because the read grant on a shared content URI lasts only as long as the intent.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Used by ShareIntakePlugin for the launch intent and
 *   for each later `onNewIntent`.
 *
 * Key invariants:
 *   - Only `ACTION_SEND` with `text/plain`, `image/*` or `audio/*` is taken; anything else is
 *     not a share and yields null.
 *   - A file larger than MAX_BYTES is refused rather than truncated, so a clip is never sent cut.
 *   - Nothing is written to disk, logged or kept: the item is built, handed on, and forgotten.
 *
 * See Also:
 *   - src/share/ for the confirmation card the human sees before anything is sent.
 *   - docs/adr/0050-android-client-capacitor-and-push-delivery.md, "The share sheet is an intake".
 */
package dev.hivemind.observation.share

import android.content.ContentResolver
import android.content.Intent
import android.net.Uri
import android.util.Base64
import com.getcapacitor.JSObject

object SharedItem {
    // Larger than a minute of compressed speech or a phone photo, far below what the bridge copes with.
    private const val MAX_BYTES = 10 * 1024 * 1024

    // Returns the item to hand to JavaScript, or null when the intent is not a share we take.
    fun from(intent: Intent?, resolver: ContentResolver): JSObject? {
        if (intent?.action != Intent.ACTION_SEND) return null
        val type = intent.type ?: return null
        return when {
            type == "text/plain" -> text(intent)
            type.startsWith("image/") -> file("image", type, intent, resolver)
            type.startsWith("audio/") -> file("audio", type, intent, resolver)
            else -> null
        }
    }

    private fun text(intent: Intent): JSObject? {
        val body = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return null
        return JSObject().put("kind", "text").put("text", body).put("mediaType", "text/plain")
    }

    private fun file(kind: String, type: String, intent: Intent, resolver: ContentResolver): JSObject {
        @Suppress("DEPRECATION")
        val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM) ?: return refused("no_file")
        val bytes = try {
            read(uri, resolver)
        } catch (failure: java.io.IOException) {
            return refused("unreadable")
        } catch (failure: SecurityException) {
            return refused("unreadable")
        } ?: return refused("too_large")
        return JSObject()
            .put("kind", kind)
            .put("base64", Base64.encodeToString(bytes, Base64.NO_WRAP))
            .put("mediaType", type)
    }

    // Reads the stream up to MAX_BYTES; one byte more means the file is too large and nothing is kept.
    private fun read(uri: Uri, resolver: ContentResolver): ByteArray? {
        val stream = resolver.openInputStream(uri) ?: throw java.io.IOException("no stream")
        stream.use {
            val out = java.io.ByteArrayOutputStream()
            val chunk = ByteArray(64 * 1024)
            while (true) {
                val n = it.read(chunk)
                if (n < 0) break
                out.write(chunk, 0, n)
                if (out.size() > MAX_BYTES) return null
            }
            return out.toByteArray()
        }
    }

    private fun refused(reason: String): JSObject = JSObject().put("kind", "refused").put("reason", reason)
}
