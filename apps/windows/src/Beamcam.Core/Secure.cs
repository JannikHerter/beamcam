// Crypto shared with the Android app (Secure.kt). Spec: docs/internals/protocol.md#identity-and-pairing
using System.Buffers.Binary;
using System.Security.Cryptography;

namespace Beamcam;

public static class Secure
{
    /// <summary>What a computer pins for a phone: SHA-256 of its certificate (DER), lowercase hex.</summary>
    public static string Fingerprint(byte[] certificateDer) => Convert.ToHexStringLower(SHA256.HashData(certificateDer));

    /// <summary>6 digits both screens show, so a user can spot a machine in the middle on first pairing.</summary>
    public static string PairingCode(string fingerprintHex)
    {
        var value = Convert.ToUInt32(fingerprintHex[..8], 16) % 1_000_000;
        var digits = value.ToString("D6");
        return $"{digits[..3]} {digits[3..]}";
    }
}

/// <summary>AES-256-GCM for UDP datagrams: u64 counter | ciphertext | 16 B tag.
/// Nonce = u32 direction | u64 counter, so each direction of a session key has its own nonce space.</summary>
public sealed class Sealer : IDisposable
{
    public const uint PhoneToComputer = 1;
    public const uint ComputerToPhone = 2;
    public const int TagBytes = 16;
    public const int Overhead = 8 + TagBytes;

    readonly AesGcm _aead;
    readonly uint _send;
    readonly uint _receive;
    readonly Lock _lock = new();
    ulong _counter;

    public Sealer(byte[] key, uint sendDirection, uint receiveDirection)
    {
        if (key.Length != 32)
            throw new ArgumentException("AES-256 key needed", nameof(key));
        _aead = new AesGcm(key, TagBytes);
        _send = sendDirection;
        _receive = receiveDirection;
    }

    public static Sealer ForComputer(byte[] key) => new(key, ComputerToPhone, PhoneToComputer);

    static byte[] Nonce(uint direction, ulong counter)
    {
        var nonce = new byte[12];
        BinaryPrimitives.WriteUInt32BigEndian(nonce, direction);
        BinaryPrimitives.WriteUInt64BigEndian(nonce.AsSpan(4), counter);
        return nonce;
    }

    public byte[] Seal(ReadOnlySpan<byte> plaintext)
    {
        var sealedData = new byte[8 + plaintext.Length + TagBytes];
        lock (_lock)
        {
            var counter = _counter++;
            BinaryPrimitives.WriteUInt64BigEndian(sealedData, counter);
            _aead.Encrypt(Nonce(_send, counter), plaintext, sealedData.AsSpan(8, plaintext.Length), sealedData.AsSpan(8 + plaintext.Length));
        }
        return sealedData;
    }

    /// <summary>The plaintext, or null if it was not sealed with this key in this direction.</summary>
    public byte[]? Open(ReadOnlySpan<byte> datagram)
    {
        if (datagram.Length < Overhead)
            return null;
        var counter = BinaryPrimitives.ReadUInt64BigEndian(datagram);
        var length = datagram.Length - Overhead;
        var plaintext = new byte[length];
        try
        {
            lock (_lock)
                _aead.Decrypt(Nonce(_receive, counter), datagram.Slice(8, length), datagram[(8 + length)..], plaintext);
            return plaintext;
        }
        catch (AuthenticationTagMismatchException)
        {
            return null;
        }
    }

    public void Dispose() => _aead.Dispose();
}
