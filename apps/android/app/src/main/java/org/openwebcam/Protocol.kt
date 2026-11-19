package org.openwebcam

import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.IOException

/** Wire format shared with the Linux app. Spec: docs/internals/protocol.md */
object Protocol {
    const val VERSION = 1
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
