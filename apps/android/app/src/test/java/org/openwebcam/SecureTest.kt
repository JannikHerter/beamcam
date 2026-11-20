package org.openwebcam

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SecureTest {
    private val key = ByteArray(32) { it.toByte() }

    @Test
    fun `sealed bytes match the Linux app`() {
        // Same vector as test_secure.py: key 00..1f, phone -> computer, counter 5.
        val sealer = Sealer.forPhone(key)
        repeat(5) { sealer.seal(ByteArray(0)) }
        assertEquals(
            "0000000000000005d1f6087b4ed1f00c8011e492984428d3374b97cfe2f157523c500e",
            sealer.seal("open webcam".toByteArray()).toHex(),
        )
    }

    @Test
    fun `computer's datagrams open, tampered and wrong-direction ones do not`() {
        val computer = Sealer(key, Sealer.COMPUTER_TO_PHONE, Sealer.PHONE_TO_COMPUTER)
        val phone = Sealer.forPhone(key)
        val sealed = computer.seal(byteArrayOf(1, 2, 3))
        val (counter, plaintext) = phone.open(sealed)!!
        assertEquals(0L, counter)
        assertArrayEquals(byteArrayOf(1, 2, 3), plaintext)
        assertNull(phone.open(sealed.copyOf().also { it[10] = (it[10] + 1).toByte() }))
        assertNull(phone.open(phone.seal(byteArrayOf(1, 2, 3)))) // its own direction
        assertNull(Sealer.forPhone(ByteArray(32)).open(sealed)) // other key
        assertNull(phone.open(sealed.copyOf(10)))
    }

    @Test
    fun `pairing code matches the Linux app`() {
        assertEquals("412 545", Secure.pairingCode(Secure.sha256("test".toByteArray())))
    }

    @Test
    fun `hex round trip rejects junk`() {
        assertArrayEquals(byteArrayOf(0, 15, -1), Secure.fromHex("000fff"))
        assertNull(Secure.fromHex("abc"))
        assertNull(Secure.fromHex("zz"))
        assertNull(Secure.fromHex("ABCD"))
    }
}
