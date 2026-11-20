// Wire format shared with the Android app. Spec: docs/internals/protocol.md
using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Beamcam;

public sealed class ProtocolException(string message, Exception? inner = null) : Exception(message, inner);

public readonly record struct Message(byte Type, byte[] Payload)
{
    public JsonObject Json()
    {
        try
        {
            return JsonNode.Parse(Encoding.UTF8.GetString(Payload)) as JsonObject
                ?? throw new ProtocolException($"message 0x{Type:x2} is not a JSON object");
        }
        catch (Exception error) when (error is JsonException or ArgumentException or InvalidOperationException)
        {
            throw new ProtocolException($"bad JSON in message 0x{Type:x2}", error);
        }
    }
}

public sealed record VideoFrame(long PtsUs, byte Flags, byte[] Data)
{
    public bool Keyframe => (Flags & Protocol.FlagKeyframe) != 0;
}

public sealed record Fragment(long FrameId, int Index, int Count, byte Flags, long PtsUs, byte[] Data);

public static class Protocol
{
    public const int Version = 2;
    public const int DefaultPort = 47800;
    public const string ServiceType = "_beamcam._tcp.local.";
    public const int MaxPayload = 8 * 1024 * 1024;

    public const byte Hello = 0x01;
    public const byte Accept = 0x02;
    public const byte End = 0x03;
    public const byte Video = 0x04;
    public const byte Camera = 0x05;
    public const byte Pending = 0x06;
    public const byte SetCamera = 0x10;

    public const byte FlagKeyframe = 0x01;
    public const byte FlagConfig = 0x02;

    public const int HeaderSize = 5;
    const int VideoHeaderSize = 9;

    public static byte[] Encode(byte type, ReadOnlySpan<byte> payload)
    {
        var message = new byte[HeaderSize + payload.Length];
        message[0] = type;
        BinaryPrimitives.WriteUInt32BigEndian(message.AsSpan(1), (uint)payload.Length);
        payload.CopyTo(message.AsSpan(HeaderSize));
        return message;
    }

    public static byte[] EncodeJson(byte type, JsonObject value) =>
        Encode(type, Encoding.UTF8.GetBytes(value.ToJsonString()));

    public static VideoFrame DecodeVideo(byte[] payload)
    {
        if (payload.Length < VideoHeaderSize)
            throw new ProtocolException("short video message");
        var pts = (long)BinaryPrimitives.ReadUInt64BigEndian(payload);
        return new VideoFrame(pts, payload[8], payload[VideoHeaderSize..]);
    }

    public static byte[] EncodeVideo(long ptsUs, byte flags, ReadOnlySpan<byte> data)
    {
        var payload = new byte[VideoHeaderSize + data.Length];
        BinaryPrimitives.WriteUInt64BigEndian(payload, (ulong)ptsUs);
        payload[8] = flags;
        data.CopyTo(payload.AsSpan(VideoHeaderSize));
        return Encode(Video, payload);
    }

    // UDP video (optional, negotiated in HELLO/ACCEPT); see docs/internals/protocol.md#udp-video.
    // These are the plaintexts inside datagrams sealed by Sealer.
    public const byte UdpVideo = 0x01;
    public const byte UdpFeedback = 0x81;
    public const byte UdpNack = 0x82;
    public const int NackWholeFrame = 0xFFFF;
    public const int MaxNackEntries = 200;
    public const int MaxDatagram = 1400;
    public const int MaxPlaintext = MaxDatagram - Sealer.Overhead;
    public const byte FeedbackKeyframe = 0x01;
    const int FragmentHeaderSize = 18;

    public static Fragment? ParseFragment(ReadOnlySpan<byte> datagram)
    {
        if (datagram.Length < FragmentHeaderSize || datagram[0] != UdpVideo)
            return null;
        var frameId = BinaryPrimitives.ReadUInt32BigEndian(datagram[1..]);
        int index = BinaryPrimitives.ReadUInt16BigEndian(datagram[5..]);
        int count = BinaryPrimitives.ReadUInt16BigEndian(datagram[7..]);
        if (count == 0 || index >= count)
            return null;
        var pts = (long)BinaryPrimitives.ReadUInt64BigEndian(datagram[10..]);
        return new Fragment(frameId, index, count, datagram[9], pts, datagram[FragmentHeaderSize..].ToArray());
    }

    public static List<byte[]> EncodeFragments(long frameId, long ptsUs, byte flags, ReadOnlySpan<byte> data, int maxPlaintext = MaxPlaintext)
    {
        var chunk = maxPlaintext - FragmentHeaderSize;
        var count = Math.Max(1, (data.Length + chunk - 1) / chunk);
        var parts = new List<byte[]>(count);
        for (var i = 0; i < count; i++)
        {
            var piece = data.Slice(i * chunk, Math.Min(chunk, data.Length - i * chunk));
            var part = new byte[FragmentHeaderSize + piece.Length];
            part[0] = UdpVideo;
            BinaryPrimitives.WriteUInt32BigEndian(part.AsSpan(1), (uint)frameId);
            BinaryPrimitives.WriteUInt16BigEndian(part.AsSpan(5), (ushort)i);
            BinaryPrimitives.WriteUInt16BigEndian(part.AsSpan(7), (ushort)count);
            part[9] = flags;
            BinaryPrimitives.WriteUInt64BigEndian(part.AsSpan(10), (ulong)ptsUs);
            piece.CopyTo(part.AsSpan(FragmentHeaderSize));
            parts.Add(part);
        }
        return parts;
    }

    public static byte[] EncodeFeedback(byte flags, long lastFrame, long lost)
    {
        var packet = new byte[10];
        packet[0] = UdpFeedback;
        packet[1] = flags;
        BinaryPrimitives.WriteUInt32BigEndian(packet.AsSpan(2), (uint)lastFrame);
        BinaryPrimitives.WriteUInt32BigEndian(packet.AsSpan(6), (uint)lost);
        return packet;
    }

    /// <summary>u8 type | u8 count | count × (u32 frame id, u16 index); index 0xFFFF = whole frame.</summary>
    public static byte[] EncodeNack(IReadOnlyList<(long FrameId, int Index)> missing)
    {
        var count = Math.Min(missing.Count, MaxNackEntries);
        var packet = new byte[2 + count * 6];
        packet[0] = UdpNack;
        packet[1] = (byte)count;
        for (var i = 0; i < count; i++)
        {
            BinaryPrimitives.WriteUInt32BigEndian(packet.AsSpan(2 + i * 6), (uint)missing[i].FrameId);
            BinaryPrimitives.WriteUInt16BigEndian(packet.AsSpan(6 + i * 6), (ushort)missing[i].Index);
        }
        return packet;
    }
}

/// <summary>Splits a byte stream into messages as data arrives, so the caller never blocks mid-message.</summary>
public sealed class MessageReader
{
    byte[] _buffer = new byte[64 * 1024];
    int _length;

    public List<Message> Feed(ReadOnlySpan<byte> data)
    {
        if (_length + data.Length > _buffer.Length)
            Array.Resize(ref _buffer, Math.Max(_buffer.Length * 2, _length + data.Length));
        data.CopyTo(_buffer.AsSpan(_length));
        _length += data.Length;

        var messages = new List<Message>();
        var offset = 0;
        while (_length - offset >= Protocol.HeaderSize)
        {
            var size = BinaryPrimitives.ReadUInt32BigEndian(_buffer.AsSpan(offset + 1));
            if (size > Protocol.MaxPayload)
                throw new ProtocolException($"message too large ({size} bytes)");
            var end = offset + Protocol.HeaderSize + (int)size;
            if (_length < end)
                break;
            messages.Add(new Message(_buffer[offset], _buffer[(offset + Protocol.HeaderSize)..end]));
            offset = end;
        }
        if (offset > 0)
        {
            Buffer.BlockCopy(_buffer, offset, _buffer, 0, _length - offset);
            _length -= offset;
        }
        return messages;
    }
}
