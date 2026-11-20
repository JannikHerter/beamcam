package org.openwebcam

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException

class ProtocolTest {
    private fun roundTrip(write: DataOutputStream.() -> Unit): DataInputStream {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).apply(write).flush()
        return DataInputStream(ByteArrayInputStream(bytes.toByteArray()))
    }

    @Test
    fun `json message survives a round trip`() {
        val input = roundTrip { writeJson(Protocol.ACCEPT, JSONObject().put("width", 1280)) }
        val message = input.readMessage()
        assertEquals(Protocol.ACCEPT, message.type)
        assertEquals(1280, message.json().getInt("width"))
    }

    @Test
    fun `header is type byte then big-endian length`() {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).writeMessage(Protocol.VIDEO, byteArrayOf(7, 8, 9))
        assertArrayEquals(byteArrayOf(0x04, 0, 0, 0, 3, 7, 8, 9), bytes.toByteArray())
    }

    @Test
    fun `video payload is pts then flags then data`() {
        val payload = encodeVideoPayload(0x0102030405060708L, Protocol.FLAG_KEYFRAME, byteArrayOf(0, 0, 0, 1, 0x65))
        assertArrayEquals(byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8, 1, 0, 0, 0, 1, 0x65), payload)
    }

    @Test(expected = ProtocolException::class)
    fun `oversized length is rejected before allocating`() {
        roundTrip { writeByte(Protocol.VIDEO); writeInt(Protocol.MAX_PAYLOAD + 1) }.readMessage()
    }

    @Test(expected = EOFException::class)
    fun `closed stream ends with EOF`() {
        DataInputStream(ByteArrayInputStream(ByteArray(0))).readMessage()
    }
}

class UdpTest {
    @Test
    fun `frame splits into datagrams with header`() {
        val data = ByteArray(Udp.MAX_DATAGRAM * 2) { it.toByte() }
        val parts = fragmentFrame(frameId = 7, ptsMicros = 0x0102030405060708L, flags = Protocol.FLAG_KEYFRAME, data = data)
        assertEquals(3, parts.size)
        assertTrue(parts.all { it.size <= Udp.MAX_DATAGRAM })
        // Same bytes as test_protocol.py's UDP vector.
        assertArrayEquals(
            byteArrayOf(0x01, 0, 0, 0, 7, 0, 1, 0, 3, 1, 1, 2, 3, 4, 5, 6, 7, 8),
            parts[1].copyOfRange(0, Udp.HEADER),
        )
        val joined = parts.flatMap { it.copyOfRange(Udp.HEADER, it.size).toList() }.toByteArray()
        assertArrayEquals(data, joined)
    }

    @Test
    fun `empty frame is one datagram`() {
        assertEquals(1, fragmentFrame(1, 0, 0, ByteArray(0)).size)
    }

    @Test
    fun `feedback parses and rejects junk`() {
        val bytes = byteArrayOf(0x81.toByte()) + ByteArray(16) { 9 } + byteArrayOf(1, 0, 0, 1, 0, 0, 0, 0, 5)
        val feedback = parseFeedback(bytes, bytes.size)!!
        assertTrue(feedback.wantsKeyframe)
        assertEquals(256L, feedback.lastFrame)
        assertEquals(5L, feedback.lost)
        assertNull(parseFeedback(bytes, 10))
        assertNull(parseFeedback(byteArrayOf(0x01) + bytes.copyOfRange(1, bytes.size), bytes.size))
    }
}

class NackTest {
    @Test
    fun `nack parses entries and rejects truncation`() {
        // Same bytes as encode_nack(bytes(16), [(258, 1), (3, 0xFFFF)]) in protocol.py.
        val bytes = byteArrayOf(0x82.toByte()) + ByteArray(16) + byteArrayOf(2, 0, 0, 1, 2, 0, 1, 0, 0, 0, 3, -1, -1)
        val (token, entries) = parseNack(bytes, bytes.size)!!
        assertArrayEquals(ByteArray(16), token)
        assertEquals(listOf(258L to 1, 3L to Udp.NACK_WHOLE_FRAME), entries)
        assertNull(parseNack(bytes, bytes.size - 1))
    }
}
