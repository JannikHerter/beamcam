package org.beamcam

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.PublicKey
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

data class Update(val version: String, val fileName: String, val size: Long, val sha256: String) {
    val url: String get() = "${Updates.DOWNLOAD_BASE}v$version/$fileName"
}

/** Reading update.json (docs/internals/updates.md). No Android here, so it is unit tested. */
object Updates {
    const val MANIFEST_URL = "https://github.com/JannikHerter/beamcam/releases/latest/download/update.json"
    const val DOWNLOAD_BASE = "https://github.com/JannikHerter/beamcam/releases/download/"

    /** The release key's certificate, as release.yml pins it. Only builds signed with it can update. */
    const val RELEASE_CERT_SHA256 = "0420f68397f561bc68e0037ca2d22e516e5fe29b82ae2d0f5365b2fea078ce83"
    private const val RELEASE_KEY =
        "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAxhDZoAUvIMPK2PUckR3rvT4bXRhwLEWUknEhsWrQmhdg5NgpbMZiqpMwumrVnKkeKZmm4gVvDJ6cZs6ftP1y8a8PDuPcapQiVD0b9poLcw2svnOEz/xJ44173lEsW1TNt46Vsg8CU+Y6a4bRC0j25Rr/GWm2+Tt03mVbqHjSSprCmfp/BPCQlwD9n7XoS9ug2N5LE7/6kJxOGzjjMssJJ+WM6KWLR7jSPBdesnyV19c1WgdxAKdoMJiFdY14Mlh5pLxtcPP308ii586LupLVMwW5Rit5q9nkebzh9VQDIS8pQKHsHPfm7h9beEvUKkqel6UyceNIJq1QerZteo8auvi9Hvo/ag34sWpkSKlZhR5SE2SLCEJ+gfRBMgwi28Rdb2ORHyN8lrv/I4JDEEH3sfbjWcHCmL7WktsIzwGxjdKSQ1Ea7AheekaRkihGTD9MwzHJPjgbpIIcng7NYAVEoCR1paLcrQekKrtitg27V4ynwUwQ/Puu2J91iG5/XtyME8QeXo1noXwC7n2l6MePYKTCDmMBXC+ltNMNKuilHlnnNaqTrvC2MJvHfTJn4EUy6VCgxxmbJVENxKGm3+g6Fuhh1mIE3sFmOcW/KlmYC0aSlLi7jkhhzaYYX6H+8qMqyCWigHiQfbohFHDY9LSQs0K63iYocwh0zfbVP4grznkCAwEAAQ=="
    private val SIGNED_PREFIX = "beamcam-update-v1\n".toByteArray()
    private const val PLATFORM = "android"
    const val MAX_MANIFEST = 64 * 1024
    const val MAX_SIGNATURE = 4096
    private const val MAX_FILE = 1L shl 30

    private val versionPattern = Regex("[0-9]{1,6}\\.[0-9]{1,6}\\.[0-9]{1,6}")
    private val fileNamePattern = Regex("[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
    private val sha256Pattern = Regex("[0-9a-f]{64}")

    val releaseKey: PublicKey by lazy { publicKey(RELEASE_KEY) }

    fun publicKey(base64: String): PublicKey =
        KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(base64)))

    /** The update in a signed update.json, if it is newer than [current]. */
    fun read(manifest: ByteArray, signature: ByteArray, current: String, key: PublicKey = releaseKey): Update? {
        val verifier = Signature.getInstance("SHA256withRSA")
        verifier.initVerify(key)
        verifier.update(SIGNED_PREFIX)
        verifier.update(manifest)
        val signed = runCatching { verifier.verify(signature) }.getOrDefault(false)
        if (!signed) return null
        return try {
            val data = JSONObject(String(manifest, Charsets.UTF_8))
            val file = data.optJSONObject("files")?.optJSONObject(PLATFORM) ?: return null
            val version = data.opt("version") as? String ?: return null
            val name = file.opt("name") as? String ?: return null
            val size = when (val value = file.opt("size")) {
                is Int -> value.toLong()
                is Long -> value
                else -> return null
            }
            val sha256 = file.opt("sha256") as? String ?: return null
            if (!isNewer(version, current) || !fileNamePattern.matches(name) || size !in 1..MAX_FILE || !sha256Pattern.matches(sha256)) return null
            Update(version, name, size, sha256)
        } catch (_: JSONException) {
            null
        }
    }

    /** True when both are X.Y.Z and [candidate] is higher. */
    fun isNewer(candidate: String, current: String): Boolean {
        if (!versionPattern.matches(candidate) || !versionPattern.matches(current)) return false
        val a = candidate.split('.').map { it.toInt() }
        val b = current.split('.').map { it.toInt() }
        for (i in 0..2) if (a[i] != b[i]) return a[i] > b[i]
        return false
    }

    /**
     * Whether the app may update itself, by what Android recorded about its install: the installer
     * and (Android 11+) the app that started the install must be nobody or adb, Beamcam itself, or
     * the system's package installer (a downloaded APK). A store (F-Droid, Google Play, any other)
     * records its own name, and then only that store updates Beamcam. Android lets an app read its
     * own initiator after that app is uninstalled, so this holds after F-Droid is removed, too.
     */
    fun mayUpdateItself(installer: String?, initiating: String?, ownPackage: String): Boolean {
        fun sideload(name: String?) = name == null || name == ownPackage || name == "com.android.shell" || name.endsWith(".packageinstaller")
        return sideload(installer) && sideload(initiating)
    }

    fun sha256Hex(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).toHex()
}

sealed interface UpdateStatus {
    val update: Update? get() = null

    data object None : UpdateStatus
    /** [failed]: the last try did not download or install. */
    data class Available(override val update: Update, val failed: Boolean = false) : UpdateStatus
    /** Downloading, or Android is installing it. */
    data class Busy(override val update: Update) : UpdateStatus
    /** Android wants the user to confirm; the window starts [intent] once it is on screen. */
    data class Confirm(override val update: Update, val intent: Intent) : UpdateStatus
}

/** Looking for, downloading and installing updates; one per process, like [WebcamState]. */
object AppUpdates {
    private const val TAG = "Beamcam"
    private const val CHANNEL_UPDATES = "updates"
    private const val NOTIFICATION_UPDATED = 3 // WebcamService uses 1 and 2
    private const val CHECK_EVERY_MILLIS = 24 * 60 * 60 * 1000L
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutable = MutableStateFlow<UpdateStatus>(UpdateStatus.None)
    val status: StateFlow<UpdateStatus> = mutable.asStateFlow()
    private var lastCheck = 0L
    private var job: Job? = null
    /** The install session whose answers count; abandoned earlier ones still send theirs. */
    private var sessionId = -1

    fun canUpdateItself(context: Context): Boolean = runCatching {
        val pm = context.packageManager
        val name = context.packageName
        val allowed = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val source = pm.getInstallSourceInfo(name)
            Updates.mayUpdateItself(source.installingPackageName, source.initiatingPackageName, name)
        } else {
            @Suppress("DEPRECATION")
            Updates.mayUpdateItself(pm.getInstallerPackageName(name), null, name)
        }
        val signers = packageInfo(context, PackageManager.GET_SIGNING_CERTIFICATES).signingInfo?.apkContentsSigners.orEmpty()
        allowed && signers.size == 1 && Updates.sha256Hex(signers[0].toByteArray()) == Updates.RELEASE_CERT_SHA256
    }.getOrDefault(false)

    private fun packageInfo(context: Context, flags: Int) = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        context.packageManager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(flags.toLong()))
    } else {
        @Suppress("DEPRECATION")
        context.packageManager.getPackageInfo(context.packageName, flags)
    }

    private fun currentVersion(context: Context) = packageInfo(context, 0).versionName.orEmpty()

    /** Looks for a new version unless that happened in the last day (or [now]). */
    @Synchronized
    fun checkIfDue(context: Context, now: Boolean = false) {
        val time = SystemClock.elapsedRealtime()
        val idle = mutable.value.let { it is UpdateStatus.None || it is UpdateStatus.Available }
        if (job?.isActive == true || !idle || (!now && lastCheck != 0L && time - lastCheck < CHECK_EVERY_MILLIS)) return
        lastCheck = time
        val app = context.applicationContext
        job = scope.launch {
            val update = try {
                val version = currentVersion(app)
                val manifest = get(Updates.MANIFEST_URL, Updates.MAX_MANIFEST, version)
                val signature = get(Updates.MANIFEST_URL + ".sig", Updates.MAX_SIGNATURE, version)
                if (manifest != null && signature != null) Updates.read(manifest, signature, version) else null
            } catch (e: IOException) {
                Log.i(TAG, "could not look for updates: $e")
                return@launch
            }
            if (update != null) Log.i(TAG, "Beamcam ${update.version} is available")
            synchronized(this@AppUpdates) {
                if (mutable.value.let { it is UpdateStatus.None || it is UpdateStatus.Available }) {
                    mutable.value = update?.let { UpdateStatus.Available(it) } ?: UpdateStatus.None
                }
            }
        }
    }

    /** The user turned updates off. An install they already started finishes. */
    @Synchronized
    fun turnedOff() {
        lastCheck = 0L
        val status = mutable.value
        if (status is UpdateStatus.Busy || status is UpdateStatus.Confirm) return
        job?.cancel()
        mutable.value = UpdateStatus.None
    }

    /** The app is on screen again: if Android's install screen went away without an answer, offer the update again. */
    @Synchronized
    fun appStarted() {
        val status = mutable.value
        if (status is UpdateStatus.Busy && job?.isActive != true) mutable.value = UpdateStatus.Available(status.update)
    }

    /** Downloads [update], checks it against update.json and hands it to Android to install. */
    @Synchronized
    fun install(context: Context, update: Update) {
        if (mutable.value !is UpdateStatus.Available) return
        job?.cancel()
        sessionId = -1 // a late answer about the last try must not end this one
        mutable.value = UpdateStatus.Busy(update)
        val app = context.applicationContext
        job = scope.launch {
            val folder = File(app.cacheDir, "updates")
            try {
                folder.deleteRecursively()
                folder.mkdirs()
                val apk = File(folder, update.fileName)
                download(update, apk, currentVersion(app))
                commit(app, apk)
                apk.delete()
            } catch (e: Exception) {
                // Also Android's session errors (SecurityException, IllegalStateException), which would end the process.
                Log.w(TAG, "update failed: $e")
                folder.deleteRecursively()
                finished(update, failed = true)
            }
        }
    }

    @Synchronized
    fun finished(update: Update, failed: Boolean) {
        mutable.value = UpdateStatus.Available(update, failed)
    }

    /** The window started Android's confirmation. */
    @Synchronized
    fun confirming(update: Update) {
        if (mutable.value is UpdateStatus.Confirm) mutable.value = UpdateStatus.Busy(update)
    }

    @Synchronized
    internal fun onInstallStatus(context: Context, intent: Intent) {
        val result = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        // This arrives in the new version's process, which knows nothing of the old one's state.
        if (result == PackageInstaller.STATUS_SUCCESS) return updated(context)
        if (intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) != sessionId) return
        // Available too: the app may have come back on screen (appStarted) before Android answered.
        val update = mutable.value.update ?: return
        when (result) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(Intent.EXTRA_INTENT)
                }
                mutable.value = if (confirm != null) UpdateStatus.Confirm(update, confirm) else UpdateStatus.Available(update, failed = true)
            }
            PackageInstaller.STATUS_FAILURE_ABORTED -> finished(update, failed = false)
            else -> {
                Log.w(TAG, "update not installed: $result ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
                finished(update, failed = true)
            }
        }
    }

    /**
     * Runs in the new version. Android closed the app to replace it and does not let it open itself
     * from the background, so a notification leads the user back.
     */
    private fun updated(context: Context) {
        val open = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val notifications = context.getSystemService(NotificationManager::class.java)
        val allowed = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        if (!allowed) return
        val text = AppLanguage.wrap(context)
        notifications.createNotificationChannel(
            NotificationChannel(CHANNEL_UPDATES, text.getString(R.string.channel_updates), NotificationManager.IMPORTANCE_DEFAULT),
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_UPDATES)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(text.getString(R.string.updated_title))
            .setContentText(text.getString(R.string.updated_text))
            .setContentIntent(PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_IMMUTABLE))
            .setAutoCancel(true)
            .build()
        notifications.notify(NOTIFICATION_UPDATED, notification)
    }

    /** The app is on screen again. */
    fun dismissUpdatedNotification(context: Context) =
        context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_UPDATED)

    private fun connect(url: String, version: String): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = 15_000
        readTimeout = 30_000
        setRequestProperty("User-Agent", "Beamcam/$version")
    }

    /** A small file, or null when the release has none (404). */
    private fun get(url: String, limit: Int, version: String): ByteArray? {
        val connection = connect(url, version)
        try {
            if (connection.responseCode == HttpURLConnection.HTTP_NOT_FOUND) return null
            if (connection.responseCode != HttpURLConnection.HTTP_OK) throw IOException("$url: HTTP ${connection.responseCode}")
            val bytes = java.io.ByteArrayOutputStream()
            connection.inputStream.use { input ->
                val chunk = ByteArray(8192)
                while (true) {
                    val read = input.read(chunk)
                    if (read < 0) break
                    if (bytes.size() + read > limit) throw IOException("$url is larger than $limit bytes")
                    bytes.write(chunk, 0, read)
                }
            }
            return bytes.toByteArray()
        } finally {
            connection.disconnect()
        }
    }

    private fun download(update: Update, target: File, version: String) {
        val connection = connect(update.url, version)
        try {
            if (connection.responseCode != HttpURLConnection.HTTP_OK) throw IOException("${update.url}: HTTP ${connection.responseCode}")
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val chunk = ByteArray(64 * 1024)
                    while (true) {
                        val read = input.read(chunk)
                        if (read < 0) break
                        total += read
                        if (total > update.size) throw IOException("the download is larger than update.json says")
                        digest.update(chunk, 0, read)
                        output.write(chunk, 0, read)
                    }
                }
            }
            if (total != update.size || digest.digest().toHex() != update.sha256) throw IOException("the download does not match update.json")
        } finally {
            connection.disconnect()
        }
    }

    private fun commit(context: Context, apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
        }
        synchronized(this) { sessionId = -1 }
        // Sessions of earlier tries that Android never finished.
        installer.mySessions.forEach { runCatching { installer.abandonSession(it.sessionId) } }
        val id = installer.createSession(params)
        synchronized(this) { sessionId = id }
        try {
            installer.openSession(id).use { session ->
                session.openWrite("beamcam.apk", 0, apk.length()).use { output ->
                    apk.inputStream().use { it.copyTo(output) }
                    session.fsync(output)
                }
                // Mutable: Android adds the result to it. Explicit and to a receiver only we can reach.
                val result = PendingIntent.getBroadcast(
                    context, id, Intent(context, UpdateReceiver::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
                )
                session.commit(result.intentSender)
            }
        } catch (e: Exception) {
            runCatching { installer.abandonSession(id) }
            throw e
        }
    }
}

/** Android's answers about an update it installs (not exported). */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = AppUpdates.onInstallStatus(context, intent)
}
