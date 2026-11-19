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

    fun isTrusted(clientId: String): Boolean = clientId in trusted()

    fun trust(clientId: String) = prefs.edit { putStringSet(KEY_TRUSTED, trusted() + clientId) }

    fun hasTrustedComputers(): Boolean = trusted().isNotEmpty()

    fun forgetComputers() = prefs.edit { remove(KEY_TRUSTED) }

    private fun trusted(): Set<String> = prefs.getStringSet(KEY_TRUSTED, emptySet()).orEmpty()

    private companion object {
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_TRUSTED = "trusted_clients"
        const val KEY_FACING = "facing"
    }
}
