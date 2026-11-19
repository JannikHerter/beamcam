package org.openwebcam

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
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
