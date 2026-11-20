using System.Text;
using System.Text.Json.Nodes;

namespace Beamcam.Tests;

public class ProtocolTests
{
    static Message Single(byte[] stream) => Assert.Single(new MessageReader().Feed(stream));

    [Fact]
    public void HeaderMatchesAndroid() =>
        // Same bytes as ProtocolTest.kt and test_protocol.py, so all sides agree on the framing.
        Assert.Equal(new byte[] { 0x04, 0, 0, 0, 3, 7, 8, 9 }, Protocol.Encode(Protocol.Video, [7, 8, 9]));

    [Fact]
    public void VideoPayloadMatchesAndroid()
    {
        var message = Single(Protocol.EncodeVideo(0x0102030405060708, Protocol.FlagKeyframe, [0, 0, 0, 1, 0x65]));
        Assert.Equal(new byte[] { 1, 2, 3, 4, 5, 6, 7, 8, 1, 0, 0, 0, 1, 0x65 }, message.Payload);
        var frame = Protocol.DecodeVideo(message.Payload);
        Assert.Equal(0x0102030405060708, frame.PtsUs);
        Assert.True(frame.Keyframe);
        Assert.Equal(new byte[] { 0, 0, 0, 1, 0x65 }, frame.Data);
    }

    [Fact]
    public void ShortVideoIsAProtocolError() => Assert.Throws<ProtocolException>(() => Protocol.DecodeVideo([1, 2, 3]));

    [Fact]
    public void JsonRoundTrip()
    {
        var stream = Protocol.EncodeJson(Protocol.Accept, new JsonObject { ["width"] = 1280 }).Concat(Protocol.Encode(Protocol.Pending, "{}"u8)).ToArray();
        var messages = new MessageReader().Feed(stream);
        Assert.Equal(1280, (int)messages[0].Json()["width"]!);
        Assert.Equal(Protocol.Pending, messages[1].Type);
    }

    [Fact]
    public void ReaderSplitsAStreamFedInPieces()
    {
        var stream = Protocol.EncodeJson(Protocol.Accept, new JsonObject { ["a"] = 1 }).Concat(Protocol.Encode(Protocol.Video, "xyz"u8)).ToArray();
        var reader = new MessageReader();
        var messages = new List<Message>();
        for (var i = 0; i < stream.Length; i++)
            messages.AddRange(reader.Feed(stream.AsSpan(i, 1)));
        Assert.Equal([(Protocol.Accept, "{\"a\":1}"), (Protocol.Video, "xyz")], messages.Select(m => (m.Type, Encoding.UTF8.GetString(m.Payload))));
    }

    [Fact]
    public void OversizedMessageIsRejected()
    {
        var header = new byte[] { Protocol.Video, 0, 0x80, 0, 1 }; // 8 MiB + 1
        Assert.Throws<ProtocolException>(() => new MessageReader().Feed(header));
    }

    [Fact]
    public void NonObjectJsonIsAProtocolError()
    {
        Assert.Throws<ProtocolException>(() => new Message(Protocol.Accept, "[1]"u8.ToArray()).Json());
        Assert.Throws<ProtocolException>(() => new Message(Protocol.Accept, "{nope"u8.ToArray()).Json());
    }

    [Fact]
    public void FragmentHeaderMatchesAndroid()
    {
        var parts = Protocol.EncodeFragments(7, 0x0102030405060708, Protocol.FlagKeyframe, new byte[Protocol.MaxPlaintext * 2]);
        Assert.Equal(3, parts.Count);
        Assert.All(parts, p => Assert.True(p.Length + Sealer.Overhead <= Protocol.MaxDatagram));
        // Same bytes as UdpTest in ProtocolTest.kt.
        Assert.Equal(new byte[] { 0x01, 0, 0, 0, 7, 0, 1, 0, 3, 1, 1, 2, 3, 4, 5, 6, 7, 8 }, parts[1][..18]);
        var fragment = Protocol.ParseFragment(parts[2])!;
        Assert.Equal((7L, 2, 3, 0x0102030405060708L), (fragment.FrameId, fragment.Index, fragment.Count, fragment.PtsUs));
    }

    [Fact]
    public void FeedbackMatchesAndroid() =>
        Assert.Equal(new byte[] { 0x81, 1, 0, 0, 1, 0, 0, 0, 0, 5 }, Protocol.EncodeFeedback(Protocol.FeedbackKeyframe, 256, 5));

    [Fact]
    public void NackMatchesAndroid() =>
        Assert.Equal(new byte[] { 0x82, 2, 0, 0, 1, 2, 0, 1, 0, 0, 0, 3, 0xFF, 0xFF },
            Protocol.EncodeNack([(258, 1), (3, Protocol.NackWholeFrame)]));

    [Fact]
    public void NackIsCappedSoItFitsADatagram()
    {
        var many = Enumerable.Range(0, 500).Select(i => ((long)i, 0)).ToList();
        var packet = Protocol.EncodeNack(many);
        Assert.Equal(Protocol.MaxNackEntries, packet[1]);
        Assert.True(packet.Length <= Protocol.MaxPlaintext);
    }

    [Fact]
    public void BadFragmentsAreIgnored()
    {
        var good = Protocol.EncodeFragments(1, 0, 0, "x"u8)[0];
        Assert.Null(Protocol.ParseFragment(good[..10]));
        Assert.Null(Protocol.ParseFragment([0x02, .. good[1..]]));
        Assert.Null(Protocol.ParseFragment([.. good[..5], 0x00, 0x05, 0x00, 0x05, .. good[9..]])); // index >= count
    }
}
