package org.beamcam

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.Display
import android.view.OrientationEventListener
import android.view.Surface
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import javax.net.ssl.SSLServerSocket
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException

/**
 * Foreground service that owns the network server, the mDNS advertisement and, while a computer
 * is connected, the camera. One computer can stream at a time.
 */
class WebcamService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var settings: Settings
    private var serverSocket: ServerSocket? = null
    private var nsdListener: NsdManager.RegistrationListener? = null
    @Volatile private var activeSession: ClientSession? = null
    private val sessionLock = Any()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun attachBaseContext(newBase: Context) = super.attachBaseContext(AppLanguage.wrap(newBase))

    /** Texts in the current language. Before Android 13 a change does not reach the attached context. */
    private fun text(id: Int, vararg args: Any): String = AppLanguage.wrap(this).getString(id, *args)

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // The language may have changed: show the notification in the new one.
        refreshTexts()
    }

    private fun refreshTexts() {
        if (serverSocket == null) return
        createChannels()
        notifyStatus((WebcamState.state.value.status as? WebcamStatus.Streaming)?.computerName)
    }

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        createChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                shutdown()
                return START_NOT_STICKY
            }
            ACTION_ALLOW -> WebcamState.respondToApproval(true)
            ACTION_DENY -> WebcamState.respondToApproval(false)
            ACTION_DISCONNECT -> activeSession?.close(Protocol.EndReason.STOPPED)
            ACTION_LANGUAGE -> refreshTexts()
            ACTION_FORGOT -> activeSession?.let { session ->
                // A computer the user just removed must not keep using the camera.
                val identity = session.identity
                if (identity != null && !session.awaitingApproval && !settings.isAllowed(identity)) {
                    session.close(Protocol.EndReason.STOPPED)
                }
            }
            ACTION_SET_FACING -> {
                val facing = Facing.valueOf(intent.getStringExtra(EXTRA_FACING) ?: Facing.FRONT.name)
                settings.facing = facing
                WebcamState.update { it.copy(facing = facing) }
                activeSession?.switchCamera(facing)
            }
        }
        if (serverSocket == null) startServer()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        activeSession?.close(Protocol.EndReason.STOPPED)
        unregisterNsd()
        serverSocket?.close()
        serverSocket = null
        scope.cancel()
        WebcamState.update { it.copy(status = WebcamStatus.Stopped) }
        super.onDestroy()
    }

    private fun startServer() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            stopSelf()
            return
        }
        // The "camera" service type exists from Android 11; older versions reject unknown types.
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA else 0
        ServiceCompat.startForeground(this, NOTIFICATION_STATUS, statusNotification(null), type)
        // Everything on TCP is TLS; the phone proves its identity with its keystore key (Identity).
        val socket = (Identity.sslContext().serverSocketFactory.createServerSocket() as SSLServerSocket).apply {
            reuseAddress = true
            enabledProtocols = supportedProtocols.filter { it == "TLSv1.3" || it == "TLSv1.2" }.toTypedArray()
        }
        try {
            socket.bind(InetSocketAddress(Protocol.DEFAULT_PORT))
        } catch (e: IOException) {
            Log.w(TAG, "port ${Protocol.DEFAULT_PORT} busy, using a random one", e)
            socket.bind(InetSocketAddress(0))
        }
        serverSocket = socket
        WebcamState.update { it.copy(status = WebcamStatus.Ready, port = socket.localPort, facing = settings.facing) }
        registerNsd(socket.localPort)
        scope.launch { acceptLoop(socket) }
    }

    private fun shutdown() {
        activeSession?.close(Protocol.EndReason.STOPPED)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private suspend fun acceptLoop(server: ServerSocket) {
        while (true) {
            val client = try {
                server.accept()
            } catch (e: SocketException) {
                return // server closed
            }
            val session = ClientSession(client)
            scope.launch {
                try {
                    session.run()
                } catch (e: Exception) {
                    Log.i(TAG, "session ended: $e")
                } finally {
                    session.cleanup()
                    val wasActive = synchronized(sessionLock) {
                        (activeSession === session).also { if (it) activeSession = null }
                    }
                    // A replaced session must not overwrite the state of the one that replaced it.
                    if (wasActive && serverSocket != null) {
                        WebcamState.update { it.copy(status = WebcamStatus.Ready) }
                        notifyStatus(null)
                    }
                    session.finished.complete(Unit)
                }
            }
        }
    }

    /**
     * Makes [session] the active one, or returns false if another computer holds the camera. See
     * [decideClaim] for the rules. [identity] is the hash of the computer's pairing secret, so only that
     * computer can replace its own session, and only allowed computers can cancel a prompt.
     */
    private suspend fun claim(session: ClientSession, identity: String?): Boolean {
        val (previous, decision) = synchronized(sessionLock) {
            val current = activeSession
            val decision = decideClaim(current != null, current?.identity, current?.awaitingApproval == true, identity)
            if (decision == Claim.BUSY) return false
            activeSession = session
            if (identity == null) session.awaitingApproval = true
            current to decision
        }
        if (previous != null) {
            if (decision == Claim.REPLACE_OWN) {
                // After a Wi-Fi drop the old session is usually dead but would hold the camera
                // until its heartbeat timed out. If not (a second app on that computer), END stops it retrying.
                Log.i(TAG, "same computer reconnected, replacing its old session")
                previous.close(Protocol.EndReason.REPLACED)
            } else {
                Log.i(TAG, "an allowed computer connected, cancelling the open prompt")
                previous.close(Protocol.EndReason.BUSY)
            }
            withTimeoutOrNull(SESSION_HANDOVER_TIMEOUT_MS) { previous.finished.await() }
            // The cancelled session is no longer active, so it does not reset the screen itself.
            WebcamState.update { if (it.status is WebcamStatus.AwaitingApproval) it.copy(status = WebcamStatus.Ready) else it }
        }
        return true
    }

    /** A control message, or (type VIDEO) one access unit with its timestamp and flags. */
    private class Outgoing(val type: Int, val payload: ByteArray, val ptsMicros: Long = 0, val flags: Int = 0)

    private inner class ClientSession(private val socket: Socket) {
        private val outgoing = Channel<Outgoing>(capacity = OUTGOING_CAPACITY)
        // Also read from the UDP receive thread (keyframe requests).
        @Volatile private var streamer: CameraStreamer? = null
        private var orientationListener: OrientationEventListener? = null
        private var wakeLock: PowerManager.WakeLock? = null
        private var wifiLock: WifiManager.WifiLock? = null
        @Volatile private var approval: CompletableDeferred<Boolean>? = null
        /** Set by [claim] for a computer that asks to pair, cleared when the user answered. */
        @Volatile var awaitingApproval = false
        @Volatile private var streaming = false
        @Volatile private var codecConfig: ByteArray? = null
        @Volatile private var waitingForKeyframe = false
        @Volatile private var quadrant = 0
        @Volatile private var lastRotation = -1
        @Volatile private var endReason: String? = null
        @Volatile private var closed = false
        @Volatile private var udpLink: UdpLink? = null
        private var udpWatchdog: Job? = null
        /** SHA-256 (hex) of the computer's pairing secret, once known. */
        @Volatile var identity: String? = null
            private set
        /** Completed once the camera and sockets are released. */
        val finished = CompletableDeferred<Unit>()

        suspend fun run() = coroutineScope {
            socket.tcpNoDelay = true
            // A small kernel buffer makes a Wi-Fi stall back up into `outgoing`, where frames are
            // dropped. Autotuned buffers hold seconds of stale video that replays as a freeze.
            socket.sendBufferSize = SEND_BUFFER_BYTES
            socket.soTimeout = HELLO_TIMEOUT_MS
            val input = DataInputStream(BufferedInputStream(socket.getInputStream()))
            val output = DataOutputStream(BufferedOutputStream(socket.getOutputStream(), 64 * 1024))
            // Closing the socket is what unblocks readLoop, so every exit path must do it.
            try {
                val hello = input.readMessage()
                if (hello.type != Protocol.HELLO) throw ProtocolException("expected HELLO")
                val json = hello.json()
                if (json.optInt("protocol") != Protocol.VERSION) {
                    output.writeJson(Protocol.END, JSONObject().put("reason", Protocol.EndReason.PROTOCOL))
                    output.flush()
                    return@coroutineScope
                }
                val transports = json.optJSONArray("transports")
                val wantsUdp = transports != null && (0 until transports.length()).any { transports.optString(it) == "udp" }
                val clientName = json.optString("clientName").ifBlank { text(R.string.unknown_computer) }.take(64)
                val presented = Secure.fromHex(json.optString("secret"))?.takeIf { it.size == Secure.SECRET_BYTES }
                identity = presented?.let { Secure.sha256(it).toHex() }
                if (identity != null && !settings.isAllowed(identity!!)) {
                    // This phone forgot that computer. Say so instead of prompting: the computer
                    // only asks again when its user clicks Connect.
                    output.writeJson(Protocol.END, JSONObject().put("reason", Protocol.EndReason.UNKNOWN))
                    output.flush()
                    return@coroutineScope
                }
                if (!claim(this@ClientSession, identity)) {
                    output.writeJson(Protocol.END, JSONObject().put("reason", Protocol.EndReason.BUSY))
                    output.flush()
                    return@coroutineScope
                }
                socket.soTimeout = 0
                launch { readLoop(input) }

                var newSecret: ByteArray? = null
                if (identity == null) {
                    output.writeJson(Protocol.PENDING, JSONObject())
                    output.flush()
                    val code = Secure.pairingCode(Identity.fingerprint())
                    val decision = WebcamState.beginApproval(clientName, code)
                    approval = decision
                    showApprovalNotification(clientName, code)
                    val allowed = withTimeoutOrNull(APPROVAL_TIMEOUT_MS) { decision.await() }
                    synchronized(sessionLock) { awaitingApproval = false }
                    approval = null
                    cancelApprovalNotification()
                    WebcamState.respondToApproval(false)
                    if (allowed != true) {
                        if (!closed) endReason = if (allowed == null) Protocol.EndReason.TIMEOUT else Protocol.EndReason.DENIED
                        sendEnd(output)
                        return@coroutineScope
                    }
                    // Cancelled by an allowed computer just as the user tapped Allow: pair nobody.
                    if (closed) {
                        sendEnd(output)
                        return@coroutineScope
                    }
                    newSecret = Secure.random(Secure.SECRET_BYTES)
                    identity = Secure.sha256(newSecret).toHex()
                }
                if (closed) {
                    sendEnd(output)
                    return@coroutineScope
                }
                settings.connected(identity!!, clientName)

                val offer = if (wantsUdp && socket.inetAddress is Inet4Address) openUdp(socket.inetAddress) else null
                val accept = JSONObject()
                    .put("protocol", Protocol.VERSION)
                    .put("deviceId", settings.deviceId)
                    .put("deviceName", settings.deviceName)
                    .put("codec", "h264")
                    .put("width", FORMAT.width)
                    .put("height", FORMAT.height)
                    .put("fps", FORMAT.fps)
                    .put("facing", settings.facing.wire)
                if (newSecret != null) accept.put("secret", newSecret.toHex())
                if (offer != null) {
                    accept.put("udpPort", offer.socket.localPort).put("udpKey", offer.key.toHex())
                }
                output.writeJson(Protocol.ACCEPT, accept)
                output.flush()
                if (offer != null) {
                    launch { offer.receiveLoop { streamer?.requestKeyframe() } }
                    if (withTimeoutOrNull(UDP_PUNCH_TIMEOUT_MS) { offer.punched.await() } != null) {
                        udpLink = offer
                        udpWatchdog = launch { watchUdp(offer) }
                        Log.i(TAG, "streaming video over UDP")
                    } else {
                        // UDP blocked on this network (or an adb port forward): stay on TCP.
                        offer.close()
                        Log.i(TAG, "no UDP from the computer, streaming video over TCP")
                    }
                }
                streaming = true
                WebcamState.update { it.copy(status = WebcamStatus.Streaming(clientName, identity!!)) }
                notifyStatus(clientName)
                acquireLocks()
                launch(Dispatchers.Main) { startOrientationListener() }
                startCamera(settings.facing)

                for (message in outgoing) {
                    write(output, message)
                    // Batch whatever is already queued into one flush.
                    while (true) {
                        write(output, outgoing.tryReceive().getOrNull() ?: break)
                    }
                    output.flush()
                }
                sendEnd(output)
            } finally {
                udpWatchdog?.cancel()
                udpLink?.let {
                    if (it.failedSends > 0) Log.i(TAG, "${it.failedSends} UDP datagrams could not be sent")
                    it.close()
                }
                runCatching { socket.close() }
            }
        }

        private fun write(output: DataOutputStream, message: Outgoing) {
            val udp = udpLink
            when {
                message.type != Protocol.VIDEO -> output.writeMessage(message.type, message.payload)
                udp != null -> udp.send(message.ptsMicros, message.flags, message.payload)
                else -> output.writeMessage(Protocol.VIDEO, encodeVideoPayload(message.ptsMicros, message.flags, message.payload))
            }
        }

        private fun openUdp(peer: InetAddress): UdpLink? = runCatching {
            val socket = DatagramSocket(0).apply { sendBufferSize = UDP_SEND_BUFFER_BYTES }
            UdpLink(socket, Secure.random(32), peer)
        }.onFailure { Log.w(TAG, "UDP unavailable", it) }.getOrNull()

        /** Over UDP nothing fails when the computer vanishes, so its FEEDBACK doubles as a heartbeat. */
        private suspend fun watchUdp(link: UdpLink) {
            while (true) {
                delay(1000)
                if (SystemClock.elapsedRealtime() - link.lastFeedbackAt > UDP_LIVENESS_TIMEOUT_MS) {
                    Log.i(TAG, "computer stopped answering over UDP")
                    outgoing.close()
                    return
                }
            }
        }

        private fun sendEnd(output: DataOutputStream) {
            val reason = endReason ?: return
            runCatching {
                output.writeJson(Protocol.END, JSONObject().put("reason", reason))
                output.flush()
            }
        }

        private fun readLoop(input: DataInputStream) {
            try {
                while (true) {
                    val message = input.readMessage()
                    if (message.type == Protocol.SET_CAMERA && streaming) {
                        val facing = Facing.fromWire(message.json().optString("facing"))
                        settings.facing = facing
                        WebcamState.update { it.copy(facing = facing) }
                        switchCamera(facing)
                    }
                }
            } catch (e: IOException) {
                // Computer went away (or we closed the socket).
                approval?.complete(false)
                outgoing.close()
            }
        }

        private fun startCamera(facing: Facing) {
            streamer = CameraStreamer(
                this@WebcamService,
                FORMAT,
                onFrame = ::onEncodedFrame,
                onError = { error ->
                    Log.e(TAG, "camera: $error")
                    WebcamState.update { it.copy(message = text(R.string.error_camera, error)) }
                    close(Protocol.EndReason.CAMERA)
                },
            ).also { it.start(facing) { sendOrientation(force = true) } }
        }

        fun switchCamera(facing: Facing) {
            streamer?.switchCamera(facing) { sendOrientation(force = true) }
        }

        private fun onEncodedFrame(ptsMicros: Long, flags: Int, data: ByteArray) {
            if (flags and Protocol.FLAG_CONFIG != 0) {
                codecConfig = data
                return
            }
            val keyframe = flags and Protocol.FLAG_KEYFRAME != 0
            if (waitingForKeyframe && !keyframe) return
            // Prepending SPS/PPS to every keyframe lets the receiver (re)start decoding at any keyframe.
            val config = codecConfig
            val unit = if (keyframe && config != null) config + data else data
            val sent = outgoing.trySend(Outgoing(Protocol.VIDEO, unit, ptsMicros, flags))
            if (sent.isSuccess) {
                waitingForKeyframe = false
            } else if (!sent.isClosed && !waitingForKeyframe) {
                // Network is slower than the encoder: skip ahead to the next keyframe to keep latency low.
                Log.i(TAG, "network backed up, skipping to the next keyframe")
                waitingForKeyframe = true
                streamer?.requestKeyframe()
            }
        }

        private fun startOrientationListener() {
            // Start from the screen's rotation so the first frames are upright before the sensor reports.
            val display = getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
            quadrant = (360 - (display?.rotation ?: Surface.ROTATION_0) * 90) % 360
            orientationListener = object : OrientationEventListener(this@WebcamService) {
                override fun onOrientationChanged(degrees: Int) {
                    val next = stableQuadrant(quadrant, degrees)
                    if (next != quadrant) {
                        quadrant = next
                        sendOrientation(force = false)
                    }
                }
            }.also { if (it.canDetectOrientation()) it.enable() }
        }

        /** Tells the computer how to rotate the picture and which camera is in use. */
        private fun sendOrientation(force: Boolean) {
            val camera = streamer?.takeIf { it.ready } ?: return
            val rotation = imageRotation(camera.sensorOrientation, quadrant, camera.frontFacing)
            if (!force && rotation == lastRotation) return
            lastRotation = rotation
            val facing = if (camera.frontFacing) Facing.FRONT else Facing.BACK
            val state = JSONObject().put("rotation", rotation).put("facing", facing.wire)
            outgoing.trySend(Outgoing(Protocol.CAMERA, state.toString().toByteArray()))
        }

        /** Ends the session; [reason] is sent as END, or nothing when null. */
        fun close(reason: String?) {
            closed = true
            endReason = reason
            approval?.complete(false)
            outgoing.close()
        }

        fun cleanup() {
            orientationListener?.let { listener -> ContextCompat.getMainExecutor(this@WebcamService).execute { listener.disable() } }
            streamer?.release()
            streamer = null
            releaseLocks()
            runCatching { socket.close() }
        }

        private fun acquireLocks() {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Beamcam:stream")
                .apply { acquire(12 * 60 * 60 * 1000L) }
            val wifiMode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiLock = applicationContext.getSystemService(WifiManager::class.java)
                .createWifiLock(wifiMode, "Beamcam:stream")
                .apply { acquire() }
        }

        private fun releaseLocks() {
            wakeLock?.takeIf { it.isHeld }?.release()
            wifiLock?.takeIf { it.isHeld }?.release()
            wakeLock = null
            wifiLock = null
        }
    }

    private fun registerNsd(port: Int) {
        val info = NsdServiceInfo().apply {
            serviceName = settings.deviceName
            serviceType = Protocol.SERVICE_TYPE
            setPort(port)
            setAttribute("id", settings.deviceId)
            setAttribute("v", Protocol.VERSION.toString())
        }
        val listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                Log.i(TAG, "mDNS registered as '${info.serviceName}' port $port")
            }

            override fun onRegistrationFailed(info: NsdServiceInfo, error: Int) {
                Log.w(TAG, "mDNS registration failed: $error")
            }

            override fun onServiceUnregistered(info: NsdServiceInfo) = Unit
            override fun onUnregistrationFailed(info: NsdServiceInfo, error: Int) = Unit
        }
        nsdListener = listener
        getSystemService(NsdManager::class.java).registerService(info, NsdManager.PROTOCOL_DNS_SD, listener)
    }

    private fun unregisterNsd() {
        nsdListener?.let { runCatching { getSystemService(NsdManager::class.java).unregisterService(it) } }
        nsdListener = null
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_STATUS, text(R.string.channel_status), NotificationManager.IMPORTANCE_LOW),
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_REQUESTS, text(R.string.channel_requests), NotificationManager.IMPORTANCE_HIGH),
        )
    }

    private fun statusNotification(streamingTo: String?): Notification {
        val builder = NotificationCompat.Builder(this, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(openAppIntent())
            .setOngoing(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        if (streamingTo == null) {
            builder.setContentTitle(text(R.string.notification_ready_title))
                .setContentText(text(R.string.notification_ready_text))
                .addAction(0, text(R.string.action_turn_off), serviceIntent(ACTION_STOP, 1))
        } else {
            builder.setContentTitle(text(R.string.notification_streaming_title, streamingTo))
                .setContentText(text(R.string.notification_streaming_text))
                .addAction(0, text(R.string.action_stop_streaming), serviceIntent(ACTION_DISCONNECT, 2))
        }
        return builder.build()
    }

    private fun notifyStatus(streamingTo: String?) {
        if (canNotify()) getSystemService(NotificationManager::class.java).notify(NOTIFICATION_STATUS, statusNotification(streamingTo))
    }

    private fun showApprovalNotification(computerName: String, code: String) {
        if (WebcamState.appVisible || !canNotify()) return
        val notification = NotificationCompat.Builder(this, CHANNEL_REQUESTS)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(text(R.string.approval_title, computerName))
            .setContentText(text(R.string.approval_text, code))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setContentIntent(openAppIntent())
            .setAutoCancel(true)
            .addAction(0, text(R.string.action_allow), serviceIntent(ACTION_ALLOW, 3))
            .addAction(0, text(R.string.action_deny), serviceIntent(ACTION_DENY, 4))
            .build()
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_APPROVAL, notification)
    }

    private fun cancelApprovalNotification() =
        getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_APPROVAL)

    private fun canNotify() = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun openAppIntent(): PendingIntent = PendingIntent.getActivity(
        this,
        0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun serviceIntent(action: String, requestCode: Int): PendingIntent = PendingIntent.getService(
        this,
        requestCode,
        Intent(this, WebcamService::class.java).setAction(action),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    companion object {
        private const val TAG = "WebcamService"
        private const val CHANNEL_STATUS = "status"
        private const val CHANNEL_REQUESTS = "requests"
        private const val NOTIFICATION_STATUS = 1
        private const val NOTIFICATION_APPROVAL = 2
        private const val HELLO_TIMEOUT_MS = 10_000
        private const val APPROVAL_TIMEOUT_MS = 60_000L
        // About a quarter second of video at 30 fps; beyond that we drop frames instead of adding lag.
        private const val OUTGOING_CAPACITY = 8
        private const val SEND_BUFFER_BYTES = 64 * 1024
        // A 720p keyframe is ~100 KB; it must fit so the burst is not dropped on the phone itself.
        private const val UDP_SEND_BUFFER_BYTES = 512 * 1024
        private const val UDP_PUNCH_TIMEOUT_MS = 1000L
        private const val UDP_LIVENESS_TIMEOUT_MS = 8000L
        private const val SESSION_HANDOVER_TIMEOUT_MS = 3000L

        val FORMAT = StreamFormat(width = 1280, height = 720, fps = 30, bitrate = 4_000_000)

        private const val ACTION_STOP = "org.beamcam.STOP"
        private const val ACTION_ALLOW = "org.beamcam.ALLOW"
        private const val ACTION_DENY = "org.beamcam.DENY"
        private const val ACTION_DISCONNECT = "org.beamcam.DISCONNECT"
        private const val ACTION_FORGOT = "org.beamcam.FORGOT"
        private const val ACTION_LANGUAGE = "org.beamcam.LANGUAGE"
        private const val ACTION_SET_FACING = "org.beamcam.SET_FACING"
        private const val EXTRA_FACING = "facing"

        fun start(context: Context) =
            ContextCompat.startForegroundService(context, Intent(context, WebcamService::class.java))

        fun stop(context: Context) =
            context.startService(Intent(context, WebcamService::class.java).setAction(ACTION_STOP))

        fun disconnect(context: Context) =
            context.startService(Intent(context, WebcamService::class.java).setAction(ACTION_DISCONNECT))

        /** Before Android 13 a running service has to be told that the app's language changed. */
        fun languageChanged(context: Context) =
            context.startService(Intent(context, WebcamService::class.java).setAction(ACTION_LANGUAGE))

        /** Tells a running service that computers were removed; it stops one that is streaming. */
        fun forgotComputers(context: Context) =
            context.startService(Intent(context, WebcamService::class.java).setAction(ACTION_FORGOT))

        fun setFacing(context: Context, facing: Facing) = context.startService(
            Intent(context, WebcamService::class.java).setAction(ACTION_SET_FACING).putExtra(EXTRA_FACING, facing.name),
        )
    }
}
