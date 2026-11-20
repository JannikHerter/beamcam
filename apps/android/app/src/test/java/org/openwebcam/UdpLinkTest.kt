package org.openwebcam

import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class UdpLinkTest {
    private val loopback = InetAddress.getLoopbackAddress()
    private val token = ByteArray(Udp.TOKEN_BYTES) { 7 }
    private val phoneSocket = DatagramSocket(InetSocketAddress(loopback, 0))
    private val computer = DatagramSocket(InetSocketAddress(loopback, 0))
    @Volatile private var networkDown = false
    private val sent = mutableListOf<ByteArray>()
    private var now = 0L

    // Like the real socket, but fails with ENETUNREACH while "Wi-Fi is off".
    private val link = UdpLink(
        phoneSocket,
        token,
        loopback,
        transmit = { packet ->
            if (networkDown) throw IOException("sendto failed: ENETUNREACH (Network is unreachable)")
            synchronized(sent) { sent += packet.data.copyOf(packet.length) }
            phoneSocket.send(packet)
        },
        clock = { now },
    )

    @After
    fun close() {
        link.close()
        computer.close()
    }

    private fun fromComputer(bytes: ByteArray) =
        computer.send(DatagramPacket(bytes, bytes.size, phoneSocket.localSocketAddress))

    private fun feedback(flags: Int = 0) =
        byteArrayOf(Udp.FEEDBACK.toByte()) + token + byteArrayOf(flags.toByte(), 0, 0, 0, 0, 0, 0, 0, 0)

    private fun nack(frameId: Int, index: Int) =
        byteArrayOf(Udp.NACK.toByte()) + token + byteArrayOf(1, 0, 0, 0, frameId.toByte(), (index shr 8).toByte(), index.toByte())

    @Test
    fun `send errors while Wi-Fi is down drop datagrams but keep the link usable`() {
        val keyframes = CountDownLatch(1)
        val receiver = thread { link.receiveLoop { keyframes.countDown() } }
        fromComputer(feedback())
        runBlocking { withTimeout(2000) { link.punched.await() } }

        networkDown = true
        link.send(0, 0, ByteArray(3000)) // must not throw
        assertEquals(3L, link.failedSends)

        // A NACK whose resend fails must not stop the receive loop...
        fromComputer(nack(frameId = 0, index = 1))
        // ...so later FEEDBACK still counts as a heartbeat and keyframe requests are still answered.
        now = 5000
        fromComputer(feedback(Udp.FEEDBACK_KEYFRAME))
        assertTrue("keyframe request ignored after a failed resend", keyframes.await(2, TimeUnit.SECONDS))
        assertEquals(5000L, link.lastFeedbackAt)

        networkDown = false
        link.send(33_000, 0, ByteArray(10))
        assertEquals(1, synchronized(sent) { sent.size })
        link.close()
        receiver.join(2000)
        assertTrue("receive loop should end when the socket closes", !receiver.isAlive)
    }

    @Test
    fun `nack resends only the requested piece`() {
        val receiver = thread { link.receiveLoop {} }
        fromComputer(feedback())
        runBlocking { withTimeout(2000) { link.punched.await() } }
        link.send(0, 0, ByteArray(3000))
        fromComputer(nack(frameId = 0, index = 2))
        val deadline = System.currentTimeMillis() + 2000
        while (synchronized(sent) { sent.size } < 4 && System.currentTimeMillis() < deadline) Thread.sleep(10)
        val resent = synchronized(sent) { sent.last() }
        assertEquals(2, ((resent[5].toInt() and 0xFF) shl 8) or (resent[6].toInt() and 0xFF))
        link.close()
        receiver.join(2000)
    }
}
