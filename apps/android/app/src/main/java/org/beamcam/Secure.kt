package org.beamcam

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Crypto shared with the Linux app (secure.py). Spec: docs/internals/protocol.md#identity-and-pairing */
object Secure {
    const val SECRET_BYTES = 32

    fun random(bytes: Int): ByteArray = ByteArray(bytes).also { SecureRandom().nextBytes(it) }

    fun sha256(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)

    /** Lower-case hex to bytes; null for anything else (including odd length). */
    fun fromHex(text: String): ByteArray? {
        if (text.length % 2 != 0 || text.any { it !in '0'..'9' && it !in 'a'..'f' }) return null
        return ByteArray(text.length / 2) { text.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    }

    /** 6 digits both screens show, so a user can spot a machine in the middle on first pairing. */
    fun pairingCode(fingerprint: ByteArray): String {
        val value = ByteBuffer.wrap(fingerprint, 0, 4).int.toLong() and 0xFFFFFFFFL
        val digits = "%06d".format(value % 1_000_000)
        return "${digits.substring(0, 3)} ${digits.substring(3)}"
    }
}

/**
 * Seals UDP datagrams with AES-256-GCM: u64 counter | ciphertext | 16 B tag.
 * Nonce = u32 direction | u64 counter, so each direction of a session key has its own nonce space.
 */
class Sealer(key: ByteArray, private val sendDirection: Int, private val receiveDirection: Int) {
    private val key = SecretKeySpec(key, "AES")
    private var counter = 0L

    init {
        require(key.size == 32) { "AES-256 key needed" }
    }

    @Synchronized
    fun seal(plaintext: ByteArray): ByteArray {
        val n = counter++
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(TAG_BITS, nonce(sendDirection, n)))
        val out = ByteBuffer.allocate(8 + plaintext.size + TAG_BYTES)
        out.putLong(n)
        out.put(cipher.doFinal(plaintext))
        return out.array()
    }

    /** (counter, plaintext), or null if the datagram was not sealed with this key in this direction. */
    fun open(datagram: ByteArray, length: Int = datagram.size): Pair<Long, ByteArray>? {
        if (length < OVERHEAD) return null
        val n = ByteBuffer.wrap(datagram, 0, 8).long
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, nonce(receiveDirection, n)))
        return try {
            n to cipher.doFinal(datagram, 8, length - 8)
        } catch (e: AEADBadTagException) {
            null
        }
    }

    companion object {
        const val PHONE_TO_COMPUTER = 1
        const val COMPUTER_TO_PHONE = 2
        const val TAG_BYTES = 16
        const val OVERHEAD = 8 + TAG_BYTES
        private const val TAG_BITS = TAG_BYTES * 8

        fun nonce(direction: Int, counter: Long): ByteArray =
            ByteBuffer.allocate(12).putInt(direction).putLong(counter).array()

        /** The phone's end of a session. */
        fun forPhone(key: ByteArray) = Sealer(key, PHONE_TO_COMPUTER, COMPUTER_TO_PHONE)
    }
}

fun ByteArray.toHex() = joinToString("") { "%02x".format(it) }
