package org.openwebcam

import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.IOException

/** Wire format shared with the Linux app. Spec: docs/internals/protocol.md */
object Protocol {
    const val VERSION = 2
    const val DEFAULT_PORT = 47800
    const val SERVICE_TYPE = "_openwebcam._tcp"
    const val MAX_PAYLOAD = 8 * 1024 * 1024

    const val HELLO: Int = 0x01
    const val ACCEPT: Int = 0x02
    const val END: Int = 0x03
    const val VIDEO: Int = 0x04
    const val CAMERA: Int = 0x05
    const val PENDING: Int = 0x06
    const val SET_CAMERA: Int = 0x10

    const val FLAG_KEYFRAME = 0x01
    const val FLAG_CONFIG = 0x02

    object EndReason {
        const val DENIED = "denied"
        const val BUSY = "busy"
        const val TIMEOUT = "timeout"
        const val STOPPED = "stopped"
        const val PROTOCOL = "protocol"
        const val CAMERA = "camera"
        const val REPLACED = "replaced"
        const val UNKNOWN = "unknown"
    }
}

class Message(val type: Int, val payload: ByteArray) {
    fun json(): JSONObject = JSONObject(String(payload, Charsets.UTF_8))
}

class ProtocolException(message: String) : IOException(message)

fun DataInputStream.readMessage(): Message {
    val type = read()
    if (type < 0) throw EOFException()
    val length = readInt()
    if (length < 0 || length > Protocol.MAX_PAYLOAD) throw ProtocolException("bad length $length")
    val payload = ByteArray(length)
    readFully(payload)
    return Message(type, payload)
}

fun DataOutputStream.writeMessage(type: Int, payload: ByteArray) {
    writeByte(type)
    writeInt(payload.size)
    write(payload)
}

fun DataOutputStream.writeJson(type: Int, json: JSONObject) =
    writeMessage(type, json.toString().toByteArray(Charsets.UTF_8))

/** VIDEO payload: u64 presentation time (µs), u8 flags, H.264 Annex-B access unit. */
fun encodeVideoPayload(ptsMicros: Long, flags: Int, data: ByteArray, offset: Int = 0, length: Int = data.size): ByteArray {
    val out = ByteArray(9 + length)
    for (i in 0 until 8) out[i] = (ptsMicros ushr (56 - 8 * i)).toByte()
    out[8] = flags.toByte()
    System.arraycopy(data, offset, out, 9, length)
    return out
}

/**
 * UDP video (optional, negotiated in HELLO/ACCEPT). These are the plaintexts inside sealed
 * datagrams ([Sealer]). Each frame is split into pieces of at most [MAX_PLAINTEXT] bytes:
 * u8 type | u32 frame id | u16 index | u16 count | u8 flags | u64 pts µs | data.
 */
object Udp {
    const val VIDEO: Int = 0x01
    const val FEEDBACK: Int = 0x81
    const val NACK: Int = 0x82
    const val NACK_WHOLE_FRAME = 0xFFFF
    const val HEADER = 18
    const val MAX_DATAGRAM = 1400
    const val MAX_PLAINTEXT = MAX_DATAGRAM - Sealer.OVERHEAD
    const val FEEDBACK_BYTES = 1 + 1 + 4 + 4
    const val FEEDBACK_KEYFRAME = 0x01
}

fun fragmentFrame(frameId: Long, ptsMicros: Long, flags: Int, data: ByteArray, maxPlaintext: Int = Udp.MAX_PLAINTEXT): List<ByteArray> {
    val chunk = maxPlaintext - Udp.HEADER
    val count = maxOf(1, (data.size + chunk - 1) / chunk)
    require(count <= 0xFFFF) { "frame too large" }
    return List(count) { index ->
        val start = index * chunk
        val length = minOf(chunk, data.size - start)
        val buffer = java.nio.ByteBuffer.allocate(Udp.HEADER + length)
        buffer.put(Udp.VIDEO.toByte())
        buffer.putInt(frameId.toInt())
        buffer.putShort(index.toShort())
        buffer.putShort(count.toShort())
        buffer.put(flags.toByte())
        buffer.putLong(ptsMicros)
        buffer.put(data, start, length)
        buffer.array()
    }
}

class Feedback(val flags: Int, val lastFrame: Long, val lost: Long) {
    val wantsKeyframe get() = flags and Udp.FEEDBACK_KEYFRAME != 0
}

/** NACK: u8 type | u8 count | count x (u32 frame id, u16 index). Null if malformed. */
fun parseNack(data: ByteArray): List<Pair<Long, Int>>? {
    if (data.size < 2 || data[0].toInt() and 0xFF != Udp.NACK) return null
    val buffer = java.nio.ByteBuffer.wrap(data)
    buffer.get()
    val count = buffer.get().toInt() and 0xFF
    if (data.size < 2 + count * 6) return null
    return List(count) { (buffer.getInt().toLong() and 0xFFFFFFFFL) to (buffer.getShort().toInt() and 0xFFFF) }
}

/** FEEDBACK: u8 type | u8 flags | u32 last frame id | u32 losses. Null if malformed. */
fun parseFeedback(data: ByteArray): Feedback? {
    if (data.size < Udp.FEEDBACK_BYTES || data[0].toInt() and 0xFF != Udp.FEEDBACK) return null
    val buffer = java.nio.ByteBuffer.wrap(data)
    buffer.get()
    val flags = buffer.get().toInt() and 0xFF
    val last = buffer.getInt().toLong() and 0xFFFFFFFFL
    val lost = buffer.getInt().toLong() and 0xFFFFFFFFL
    return Feedback(flags, last, lost)
}
