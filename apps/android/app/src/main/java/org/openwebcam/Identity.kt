package org.openwebcam

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.security.auth.x500.X500Principal

/**
 * This phone's TLS identity: an EC key that never leaves Android's keystore, with the self-signed
 * certificate the keystore creates for it. Computers pin its fingerprint when they pair, so it
 * must stay the same across app restarts and updates (it is lost on uninstall, which is fine:
 * then the phone also forgets its allowed computers).
 */
object Identity {
    private const val ALIAS = "open-webcam-tls"

    private val keyStore: KeyStore by lazy { KeyStore.getInstance("AndroidKeyStore").apply { load(null) } }

    @Synchronized
    fun ensure() {
        if (keyStore.containsAlias(ALIAS)) return
        val now = System.currentTimeMillis()
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(java.security.spec.ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA384, KeyProperties.DIGEST_SHA512)
            .setCertificateSubject(X500Principal("CN=Open Webcam"))
            .setCertificateSerialNumber(BigInteger.ONE)
            .setCertificateNotBefore(Date(now - 24L * 3600 * 1000))
            .setCertificateNotAfter(Date(now + 50L * 365 * 24 * 3600 * 1000))
            .build()
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply { initialize(spec) }.generateKeyPair()
    }

    fun certificate(): X509Certificate {
        ensure()
        return keyStore.getCertificate(ALIAS) as X509Certificate
    }

    /** SHA-256 of the certificate, what computers pin. */
    fun fingerprint(): ByteArray = MessageDigest.getInstance("SHA-256").digest(certificate().encoded)

    fun sslContext(): SSLContext {
        ensure()
        val factory = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        factory.init(keyStore, null)
        return SSLContext.getInstance("TLS").apply { init(factory.keyManagers, null, null) }
    }
}
