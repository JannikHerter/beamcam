package org.openwebcam

import android.content.Context
import android.os.Build
import android.provider.Settings as AndroidSettings
import androidx.core.content.edit
import java.util.UUID

/** Small persistent settings: this phone's id, trusted computers, preferred camera. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("open_webcam", Context.MODE_PRIVATE)
    private val appContext = context.applicationContext

    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: UUID.randomUUID().toString().also {
            prefs.edit { putString(KEY_DEVICE_ID, it) }
        }

    val deviceName: String
        get() = AndroidSettings.Global.getString(appContext.contentResolver, AndroidSettings.Global.DEVICE_NAME)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: Build.MODEL

    var facing: Facing
        get() = runCatching { Facing.valueOf(prefs.getString(KEY_FACING, null) ?: "") }.getOrDefault(Facing.FRONT)
        set(value) = prefs.edit { putString(KEY_FACING, value.name) }

    /** Allowed computers are the SHA-256 (hex) of the secret each one got when it paired. */
    fun isAllowed(secretHash: String): Boolean = secretHash in allowed()

    fun allow(secretHash: String) = prefs.edit { putStringSet(KEY_ALLOWED, allowed() + secretHash) }

    fun forgetComputers() = prefs.edit {
        remove(KEY_ALLOWED)
        remove(KEY_LEGACY_TRUSTED)
    }

    private fun allowed(): Set<String> = prefs.getStringSet(KEY_ALLOWED, emptySet()).orEmpty()

    private companion object {
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_ALLOWED = "allowed_secret_hashes"
        // Protocol 1 trusted plain client ids; they grant nothing now and are dropped on "forget".
        const val KEY_LEGACY_TRUSTED = "trusted_clients"
        const val KEY_FACING = "facing"
    }
}
