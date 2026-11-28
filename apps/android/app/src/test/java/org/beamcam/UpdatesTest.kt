package org.beamcam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64

class UpdatesTest {
    private val keys = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.genKeyPair()

    private fun sign(manifest: ByteArray, prefix: String = "beamcam-update-v1\n"): ByteArray =
        Signature.getInstance("SHA256withRSA").run {
            initSign(keys.private)
            update(prefix.toByteArray())
            update(manifest)
            sign()
        }

    private fun manifest(
        version: String = "1.2.3",
        name: String = "Beamcam-1.2.3.apk",
        size: String = "5",
        sha256: String = "45569da57f4b7bf472d7a864ef4781451cae6383fee9fb0ae40c59aa1ce475b7",
    ) = """{"version": "$version", "files": {"android": {"name": "$name", "size": $size, "sha256": "$sha256"}}}""".toByteArray()

    private fun read(manifest: ByteArray, current: String = "1.0.0") = Updates.read(manifest, sign(manifest), current, keys.public)

    @Test
    fun `reads what the release script signed`() {
        // The same files as UpdatesTests.cs: scripts/sign-update.sh 1.2.3 with a throwaway test key.
        val decode = Base64.getDecoder()
        val manifest = decode.decode(
            "ewogICJ2ZXJzaW9uIjogIjEuMi4zIiwKICAiZmlsZXMiOiB7CiAgICAiYW5kcm9pZCI6IHsKICAgICAgIm5hbWUiOiAiQmVhbWNhbS0xLjIuMy5hcGsiLAogICAgICAic2l6ZSI6IDUsCiAgICAgICJzaGEyNTYiOiAiNDU1NjlkYTU3ZjRiN2JmNDcyZDdhODY0ZWY0NzgxNDUxY2FlNjM4M2ZlZTlmYjBhZTQwYzU5YWExY2U0NzViNyIKICAgIH0sCiAgICAid2luZG93cyI6IHsKICAgICAgIm5hbWUiOiAiQmVhbWNhbS1TZXR1cC0xLjIuMy5leGUiLAogICAgICAic2l6ZSI6IDE3LAogICAgICAic2hhMjU2IjogImJhNDRhYjExZDcyZTVmMjUyOTQ2MTY4ZDkzMTQwNGU0OTY1MjE5MGVlYjcwMmFiOTdlNjViMjc1YWJkNzhhYjMiCiAgICB9CiAgfQp9Cg==",
        )
        val signature = decode.decode(
            "kJajP/IARXk9SX7TPcdJwfp0386toGVQA9eSL7GgGuDlaSTkbJOFzXKqKyRhw6P7VJ0IlZ+FLCAlMDkB5FBO05X8O29BzjyL8iUJ956HznGTx46KBcfXwwOC/oijp0x60icc47o/c6xTnXMfscTFn6mAI/M2I36bp1FNcKQfF0G1bIH6OZeQBsmOJDvVQn7LGsfKCSkHbgWYGXVthdTQVt7yd/kBcpypw5kL8dc+ySwUlM4MP5jm/4wRVx7/WBY/WZZpo+5BnGD2WdDtdLIV6SqbaRHn5elnsyu89ndHL82G//zbsuQ3LghKaqjX1jBt8IPW+bYdWmdsZK+lD4Fn4g==",
        )
        val key = Updates.publicKey(
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwr182JGttRralMAGoZtUjEcs3Bw+jOUy4khIRG36eH2JIDPGcuYY+BTsuzRnjSHYINhe43ZvSZArV4fWAOZ0FObm5MCJn8uHkuMb4WxkOm3txADNdxUF8u5VGya5gyAbFFpr21ScGxWiTag83OnFi1PIT+dhw7WlsH5wW8lCdXbov3mYtgb7AhAZavR9/IqvkeZxd78Auu4yakzss7ZGWfD0l0G7/5UDlbSMgqKHowiBsQYnu/2U8yPvXUYYiOhKW5XqGfC2E1newW3jF5TMpDeyLD66fPDdZP/GOBQXc1wqxmDr0lsyPC85uRDdLhHankhi94LMaLAyXMhF6CqJzwIDAQAB",
        )
        val update = Updates.read(manifest, signature, "1.2.2", key)
        assertEquals(Update("1.2.3", "Beamcam-1.2.3.apk", 5, "45569da57f4b7bf472d7a864ef4781451cae6383fee9fb0ae40c59aa1ce475b7"), update)
        assertEquals("https://github.com/JannikHerter/beamcam/releases/download/v1.2.3/Beamcam-1.2.3.apk", update!!.url)
    }

    @Test
    fun `drops anything not signed by the key`() {
        val manifest = manifest()
        val signature = sign(manifest)
        assertNotNull(Updates.read(manifest, signature, "1.0.0", keys.public))
        assertNull(Updates.read(manifest(version = "1.2.4"), signature, "1.0.0", keys.public))
        assertNull(Updates.read(manifest, sign(manifest, prefix = ""), "1.0.0", keys.public))
        assertNull(Updates.read(manifest, ByteArray(3), "1.0.0", keys.public))
        val other = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.genKeyPair()
        assertNull(Updates.read(manifest, signature, "1.0.0", other.public))
    }

    @Test
    fun `only offers newer versions`() {
        assertNotNull(read(manifest(), current = "1.2.2"))
        assertNull(read(manifest(), current = "1.2.3"))
        assertNull(read(manifest(), current = "1.10.0"))
        assertNull(read(manifest(version = "1.3.0-rc.1")))
    }

    @Test
    fun `compares versions by number`() {
        assertTrue(Updates.isNewer("1.0.10", "1.0.9"))
        assertTrue(Updates.isNewer("2.0.0", "1.99.99"))
        assertFalse(Updates.isNewer("1.0.0", "1.0.0"))
        assertFalse(Updates.isNewer("0.9.0", "1.0.0"))
        assertFalse(Updates.isNewer("1.0", "0.9.0"))
        assertFalse(Updates.isNewer("1.0.0\n", "0.9.0"))
        assertFalse(Updates.isNewer("1.0.0", "garbage"))
    }

    @Test
    fun `refuses file names that leave the release`() {
        for (name in listOf("../Beamcam.apk", "a/b.apk", ".apk", "")) assertNull(name, read(manifest(name = name)))
    }

    @Test
    fun `refuses broken file entries`() {
        assertNull(read(manifest(size = "0")))
        assertNull(read(manifest(size = "\"5\"")))
        assertNull(read(manifest(size = "5.5")))
        assertNull(read(manifest(sha256 = "45569DA57F4B7BF472D7A864EF4781451CAE6383FEE9FB0AE40C59AA1CE475B7")))
        assertNull(read(manifest(sha256 = "1234")))
    }

    @Test
    fun `ignores other platforms and broken json`() {
        assertNull(read("""{"version": "1.2.3", "files": {"windows": {"name": "Beamcam-Setup-1.2.3.exe", "size": 17, "sha256": "ba44ab11d72e5f252946168d931404e49652190eeb702ab97e65b275abd78ab3"}}}""".toByteArray()))
        assertNull(read("not json".toByteArray()))
        assertNull(read("[]".toByteArray()))
    }

    @Test
    fun `only downloaded APKs update themselves, never store installs`() {
        val own = "org.beamcam"
        val installer = "com.google.android.packageinstaller"
        // What Android recorded on the test phones: adb, a downloaded APK, an update by Beamcam itself.
        assertTrue(Updates.mayUpdateItself(null, "com.android.shell", own))
        assertTrue(Updates.mayUpdateItself(installer, installer, own))
        assertTrue(Updates.mayUpdateItself(own, own, own))
        assertTrue(Updates.mayUpdateItself("com.android.packageinstaller", null, own)) // Android 9 and 10
        for (store in listOf("org.fdroid.fdroid", "org.fdroid.basic", "com.android.vending", "com.looker.droidify",
            "com.sec.android.app.samsungapps", "com.aurora.store", "dev.imranr.obtainium")) {
            assertFalse(store, Updates.mayUpdateItself(store, store, own))
            // F-Droid was uninstalled later: Android forgets the installer but keeps who started it.
            assertFalse(store, Updates.mayUpdateItself(null, store, own))
        }
    }
}
