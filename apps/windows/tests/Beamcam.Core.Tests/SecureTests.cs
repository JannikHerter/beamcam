using System.Security.Cryptography;
using System.Text;

namespace Beamcam.Tests;

public class SecureTests
{
    static readonly byte[] Key = Enumerable.Range(0, 32).Select(i => (byte)i).ToArray();

    [Fact]
    public void PhoneVectorMatchesAndroid()
    {
        // Same vector as SecureTest.kt and test_secure.py: key 00..1f, phone -> computer, counter 5.
        var sealedData = Convert.FromHexString("0000000000000005dce30c780dc7f8121966254e4c0e7dbe5911f64374d135");
        using var computer = Sealer.ForComputer(Key);
        Assert.Equal("beamcam"u8.ToArray(), computer.Open(sealedData));
    }

    [Fact]
    public void RoundTripAndRejections()
    {
        using var computer = Sealer.ForComputer(Key);
        using var phone = new Sealer(Key, Sealer.PhoneToComputer, Sealer.ComputerToPhone);
        var sealedData = computer.Seal("hello"u8);
        Assert.Equal("hello"u8.ToArray(), phone.Open(sealedData));
        Assert.Null(computer.Open(sealedData)); // own direction
        var tampered = (byte[])sealedData.Clone();
        tampered[9] ^= 1;
        Assert.Null(phone.Open(tampered));
        Assert.Null(phone.Open(sealedData[..10]));
        Assert.NotEqual(sealedData[..8], computer.Seal("hello"u8)[..8]); // counter advances
    }

    [Fact]
    public void PairingCodeMatchesAndroid() =>
        Assert.Equal("412 545", Secure.PairingCode(Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes("test")))));

    [Fact]
    public void PairingCodeKeepsLeadingZeros() => Assert.Equal("000 000", Secure.PairingCode("000f4240" + new string('0', 56)));

    [Fact]
    public void FingerprintIsLowercaseHexOfTheCertificate() =>
        Assert.Equal("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", Secure.Fingerprint(Encoding.UTF8.GetBytes("test")));
}
