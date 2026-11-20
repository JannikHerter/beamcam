package org.openwebcam

import android.os.SystemClock
import kotlinx.coroutines.CompletableDeferred
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketAddress

/**
 * Video over UDP to the address the computer's first FEEDBACK came from (so firewalls see replies).
 *
 * A failed send is a lost datagram, which UDP has to survive anyway: it never ends the session.
 * When Wi-Fi drops, sends fail with ENETUNREACH until it is back; whether the computer is gone is
 * decided only by FEEDBACK going quiet ([lastFeedbackAt]) or the TCP connection closing.
 */
class UdpLink(
    val socket: DatagramSocket,
    val token: ByteArray,
    private val peer: InetAddress,
    private val transmit: (DatagramPacket) -> Unit = socket::send,
    private val clock: () -> Long = SystemClock::elapsedRealtime,
) {
    @Volatile var target: SocketAddress? = null
        private set
    val punched = CompletableDeferred<Unit>()
    @Volatile var lastFeedbackAt = clock()
        private set
    /** Datagrams that could not be sent, for logs and tests. */
    @Volatile var failedSends = 0L
        private set
    private var frameId = 0L
    private var lastKeyframeRequest = 0L
    // Recently sent datagrams by frame id, for resending what the computer reports missing.
    private val history = object : LinkedHashMap<Long, List<ByteArray>>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Long, List<ByteArray>>) = size > RESEND_HISTORY_FRAMES
    }

    fun send(ptsMicros: Long, flags: Int, data: ByteArray) {
        val to = target ?: return
        val id = frameId++
        val parts = fragmentFrame(id, ptsMicros, flags, data)
        synchronized(history) { history[id] = parts }
        for (part in parts) trySend(part, to)
    }

    private fun resend(entries: List<Pair<Long, Int>>) {
        val to = target ?: return
        for ((id, index) in entries) {
            val parts = synchronized(history) { history[id] } ?: continue
            val wanted = if (index == Udp.NACK_WHOLE_FRAME) parts else listOfNotNull(parts.getOrNull(index))
            for (part in wanted) trySend(part, to)
        }
    }

    private fun trySend(part: ByteArray, to: SocketAddress) {
        try {
            transmit(DatagramPacket(part, part.size, to))
        } catch (e: IOException) {
            failedSends++
        }
    }

    /** Runs until the socket is closed. */
    fun receiveLoop(onKeyframeRequest: () -> Unit) {
        val buffer = ByteArray(2048)
        val packet = DatagramPacket(buffer, buffer.size)
        while (!socket.isClosed) {
            try {
                packet.setLength(buffer.size)
                socket.receive(packet)
            } catch (e: IOException) {
                if (socket.isClosed) return
                Thread.sleep(RECEIVE_ERROR_BACKOFF_MS)
                continue
            }
            if (packet.address != peer) continue
            val nack = parseNack(buffer, packet.length)
            if (nack != null) {
                if (nack.first.contentEquals(token)) resend(nack.second)
                continue
            }
            val feedback = parseFeedback(buffer, packet.length) ?: continue
            if (!feedback.token.contentEquals(token)) continue
            val now = clock()
            lastFeedbackAt = now
            if (target == null) {
                target = packet.socketAddress
                punched.complete(Unit)
            }
            // A lost burst produces several requests; one keyframe answers all of them.
            if (feedback.wantsKeyframe && now - lastKeyframeRequest > KEYFRAME_REQUEST_INTERVAL_MS) {
                lastKeyframeRequest = now
                onKeyframeRequest()
            }
        }
    }

    fun close() = socket.close()

    companion object {
        const val KEYFRAME_REQUEST_INTERVAL_MS = 200L
        const val RESEND_HISTORY_FRAMES = 90
        private const val RECEIVE_ERROR_BACKOFF_MS = 50L
    }
}
