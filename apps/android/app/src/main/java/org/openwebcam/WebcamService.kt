package org.openwebcam

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
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
import java.net.InetSocketAddress
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

    override fun onBind(intent: Intent?): IBinder? = null

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
        val socket = ServerSocket().apply { reuseAddress = true }
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
            if (activeSession != null) {
                scope.launch { rejectBusy(client) }
                continue
            }
            val session = ClientSession(client)
            activeSession = session
            scope.launch {
                try {
                    session.run()
                } catch (e: Exception) {
                    Log.i(TAG, "session ended: $e")
                } finally {
                    session.cleanup()
                    activeSession = null
                    if (serverSocket != null) {
                        WebcamState.update { it.copy(status = WebcamStatus.Ready) }
                        notifyStatus(null)
                    }
                }
            }
        }
    }

    private fun rejectBusy(client: Socket) = client.use {
        runCatching {
            DataOutputStream(it.getOutputStream()).writeJson(Protocol.END, JSONObject().put("reason", Protocol.EndReason.BUSY))
        }
    }

    private class Outgoing(val type: Int, val payload: ByteArray, val isFrame: Boolean = false, val isKeyframe: Boolean = false)

    private inner class ClientSession(private val socket: Socket) {
        private val outgoing = Channel<Outgoing>(capacity = OUTGOING_CAPACITY)
        private var streamer: CameraStreamer? = null
        private var orientationListener: OrientationEventListener? = null
        private var wakeLock: PowerManager.WakeLock? = null
        private var wifiLock: WifiManager.WifiLock? = null
        @Volatile private var approval: CompletableDeferred<Boolean>? = null
        @Volatile private var streaming = false
        @Volatile private var codecConfig: ByteArray? = null
        @Volatile private var waitingForKeyframe = false
        @Volatile private var quadrant = 0
        @Volatile private var lastRotation = -1
        @Volatile private var endReason: String? = null

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
                val clientId = json.optString("clientId")
                val clientName = json.optString("clientName").ifBlank { getString(R.string.unknown_computer) }.take(64)
                socket.soTimeout = 0
                launch { readLoop(input) }

                if (clientId.isBlank() || !settings.isTrusted(clientId)) {
                    output.writeJson(Protocol.PENDING, JSONObject())
                    output.flush()
                    val decision = WebcamState.beginApproval(clientName)
                    approval = decision
                    showApprovalNotification(clientName)
                    val allowed = withTimeoutOrNull(APPROVAL_TIMEOUT_MS) { decision.await() }
                    approval = null
                    cancelApprovalNotification()
                    WebcamState.respondToApproval(false)
                    if (allowed != true) {
                        endReason = if (allowed == null) Protocol.EndReason.TIMEOUT else Protocol.EndReason.DENIED
                        sendEnd(output)
                        return@coroutineScope
                    }
                    if (clientId.isNotBlank()) settings.trust(clientId)
                }
                if (endReason != null) return@coroutineScope

                output.writeJson(
                    Protocol.ACCEPT,
                    JSONObject()
                        .put("protocol", Protocol.VERSION)
                        .put("deviceId", settings.deviceId)
                        .put("deviceName", settings.deviceName)
                        .put("codec", "h264")
                        .put("width", FORMAT.width)
                        .put("height", FORMAT.height)
                        .put("fps", FORMAT.fps)
                        .put("facing", settings.facing.wire),
                )
                output.flush()
                streaming = true
                WebcamState.update { it.copy(status = WebcamStatus.Streaming(clientName)) }
                notifyStatus(clientName)
                acquireLocks()
                launch(Dispatchers.Main) { startOrientationListener() }
                startCamera(settings.facing)

                for (message in outgoing) {
                    output.writeMessage(message.type, message.payload)
                    // Batch whatever is already queued into one flush.
                    while (true) {
                        val next = outgoing.tryReceive().getOrNull() ?: break
                        output.writeMessage(next.type, next.payload)
                    }
                    output.flush()
                }
                sendEnd(output)
            } finally {
                runCatching { socket.close() }
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
                    WebcamState.update { it.copy(message = getString(R.string.error_camera, error)) }
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
            val sent = outgoing.trySend(
                Outgoing(Protocol.VIDEO, encodeVideoPayload(ptsMicros, flags, unit), isFrame = true, isKeyframe = keyframe),
            )
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

        fun close(reason: String) {
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
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "OpenWebcam:stream")
                .apply { acquire(12 * 60 * 60 * 1000L) }
            val wifiMode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiLock = applicationContext.getSystemService(WifiManager::class.java)
                .createWifiLock(wifiMode, "OpenWebcam:stream")
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
            NotificationChannel(CHANNEL_STATUS, getString(R.string.channel_status), NotificationManager.IMPORTANCE_LOW),
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_REQUESTS, getString(R.string.channel_requests), NotificationManager.IMPORTANCE_HIGH),
        )
    }

    private fun statusNotification(streamingTo: String?): Notification {
        val builder = NotificationCompat.Builder(this, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(openAppIntent())
            .setOngoing(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        if (streamingTo == null) {
            builder.setContentTitle(getString(R.string.notification_ready_title))
                .setContentText(getString(R.string.notification_ready_text))
                .addAction(0, getString(R.string.action_turn_off), serviceIntent(ACTION_STOP, 1))
        } else {
            builder.setContentTitle(getString(R.string.notification_streaming_title, streamingTo))
                .setContentText(getString(R.string.notification_streaming_text))
                .addAction(0, getString(R.string.action_stop_streaming), serviceIntent(ACTION_DISCONNECT, 2))
        }
        return builder.build()
    }

    private fun notifyStatus(streamingTo: String?) {
        if (canNotify()) getSystemService(NotificationManager::class.java).notify(NOTIFICATION_STATUS, statusNotification(streamingTo))
    }

    private fun showApprovalNotification(computerName: String) {
        if (WebcamState.appVisible || !canNotify()) return
        val notification = NotificationCompat.Builder(this, CHANNEL_REQUESTS)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.approval_title, computerName))
            .setContentText(getString(R.string.approval_text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setContentIntent(openAppIntent())
            .setAutoCancel(true)
            .addAction(0, getString(R.string.action_allow), serviceIntent(ACTION_ALLOW, 3))
            .addAction(0, getString(R.string.action_deny), serviceIntent(ACTION_DENY, 4))
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

        val FORMAT = StreamFormat(width = 1280, height = 720, fps = 30, bitrate = 4_000_000)

        private const val ACTION_STOP = "org.openwebcam.STOP"
        private const val ACTION_ALLOW = "org.openwebcam.ALLOW"
        private const val ACTION_DENY = "org.openwebcam.DENY"
        private const val ACTION_DISCONNECT = "org.openwebcam.DISCONNECT"
        private const val ACTION_SET_FACING = "org.openwebcam.SET_FACING"
        private const val EXTRA_FACING = "facing"

        fun start(context: Context) =
            ContextCompat.startForegroundService(context, Intent(context, WebcamService::class.java))

        fun stop(context: Context) =
            context.startService(Intent(context, WebcamService::class.java).setAction(ACTION_STOP))

        fun disconnect(context: Context) =
            context.startService(Intent(context, WebcamService::class.java).setAction(ACTION_DISCONNECT))

        fun setFacing(context: Context, facing: Facing) = context.startService(
            Intent(context, WebcamService::class.java).setAction(ACTION_SET_FACING).putExtra(EXTRA_FACING, facing.name),
        )
    }
}
