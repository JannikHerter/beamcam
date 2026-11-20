"""Turns UDP fragments back into frames, in order.

Missing pieces are reported by `missing()` as soon as a later packet shows the gap, so the caller
can ask the phone to resend them (NACK). A frame still incomplete after LOSS_TIMEOUT is lost. After
a loss the following P-frames would decode as smeared garbage, so frames are held back until the
next keyframe; while `waiting_for_keyframe` is true the caller should keep asking for one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from beamcam.protocol import FLAG_KEYFRAME, NACK_WHOLE_FRAME, Fragment, VideoFrame

# How long the stream waits for a missing piece once newer packets have arrived. Covers a few
# resend round trips on Wi-Fi (5-30 ms each); after that a keyframe recovers faster.
LOSS_TIMEOUT = 0.15
NACK_RETRY = 0.03
# A gap of more frames than this is an outage, not packet loss: resending would only deliver stale
# video in a burst, so give up at once and recover with a keyframe. About 165 ms at 30 fps.
MAX_RECOVER_FRAMES = 5
MAX_PENDING = 120


@dataclass
class _Pending:
    count: int
    flags: int
    pts_us: int
    first_seen: float
    pieces: dict[int, bytes] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return len(self.pieces) == self.count

    def frame(self) -> VideoFrame:
        return VideoFrame(self.pts_us, self.flags, b"".join(self.pieces[i] for i in range(self.count)))


class Reassembler:
    def __init__(self, loss_timeout: float = LOSS_TIMEOUT) -> None:
        self._window = loss_timeout
        self._pending: dict[int, _Pending] = {}
        self._newest: int | None = None
        self._gap_seen: dict[int, float] = {}  # frame id -> when a later packet first showed it incomplete
        self._nacked: dict[tuple[int, int], float] = {}
        self._next: int | None = None  # next frame id to deliver; None until a keyframe arrives
        self.losses = 0
        self.last_frame = 0

    @property
    def waiting_for_keyframe(self) -> bool:
        return self._next is None

    def add(self, fragment: Fragment, now: float) -> list[VideoFrame]:
        fid = fragment.frame_id
        if self._next is not None and fid < self._next:
            return self.poll(now)  # late duplicate or part of a frame we gave up on
        pending = self._pending.get(fid)
        if pending is None:
            if len(self._pending) >= MAX_PENDING:
                self._pending.pop(min(self._pending))
            pending = self._pending[fid] = _Pending(fragment.count, fragment.flags, fragment.pts_us, now)
        if fragment.count == pending.count:
            pending.pieces[fragment.index] = fragment.data
        if self._newest is None or fid > self._newest:
            self._newest = fid
        return self.poll(now)

    def missing(self, now: float) -> list[tuple[int, int]]:
        """(frame id, index) pieces that a later packet proves missing and that were not asked for
        in the last NACK_RETRY. Index NACK_WHOLE_FRAME means no piece of that frame arrived."""
        if self._newest is None:
            return []
        start = self._next if self._next is not None else min(self._pending, default=self._newest)
        out: list[tuple[int, int]] = []
        for fid in range(max(start, self._newest - MAX_RECOVER_FRAMES), self._newest + 1):
            pending = self._pending.get(fid)
            if pending is None:
                gaps = [NACK_WHOLE_FRAME] if fid < self._newest else []
            elif pending.complete:
                continue
            else:
                last_seen = max(pending.pieces)
                gaps = [i for i in range(pending.count) if i not in pending.pieces and (i < last_seen or fid < self._newest)]
            if not gaps:
                continue
            self._gap_seen.setdefault(fid, now)
            for index in gaps:
                if now - self._nacked.get((fid, index), -1.0) >= NACK_RETRY:
                    self._nacked[(fid, index)] = now
                    out.append((fid, index))
        return out

    def poll(self, now: float) -> list[VideoFrame]:
        out: list[VideoFrame] = []
        while True:
            if self._next is None:
                keyframes = [fid for fid, p in self._pending.items() if p.complete and p.flags & FLAG_KEYFRAME]
                if not keyframes:
                    break
                self._skip_to(min(keyframes))
                continue
            pending = self._pending.get(self._next)
            if pending is not None and pending.complete:
                del self._pending[self._next]
                self._forget(self._next)
                out.append(pending.frame())
                self.last_frame = self._next
                self._next += 1
                continue
            if self._overdue(now):
                self.losses += 1
                self._next = None
                continue
            break
        return out

    def _overdue(self, now: float) -> bool:
        """The awaited frame is given up once newer packets have shown it incomplete for LOSS_TIMEOUT."""
        assert self._next is not None
        if self._newest is None or self._newest <= self._next:
            return False
        if self._newest - self._next > MAX_RECOVER_FRAMES:
            return True
        newer = (p.first_seen for fid, p in self._pending.items() if fid > self._next)
        since = self._gap_seen.get(self._next, min(newer, default=now))
        return now - since > self._window

    def _skip_to(self, frame_id: int) -> None:
        for fid in [f for f in self._pending if f < frame_id]:
            del self._pending[fid]
        for fid in [f for f in self._gap_seen if f < frame_id]:
            self._forget(fid)
        self._next = frame_id

    def _forget(self, frame_id: int) -> None:
        self._gap_seen.pop(frame_id, None)
        for key in [k for k in self._nacked if k[0] <= frame_id]:
            del self._nacked[key]
