"""Where raw socket frames come from.

A frame source is an async iterator of :class:`RawFrame` and nothing else: it
does not know what a frame means, only where it came from and when. That is
what lets the same parser run against a live page and against a log recorded
weeks earlier — the pipeline downstream cannot tell the two apart, so a bug
found in production is reproducible offline.

Playwright hands frames over through **synchronous callbacks**, which cannot
be awaited and must not block. The bridge is a queue: the callback pushes, the
iterator pulls, and the handler returns immediately either way.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .health import HealthLog
from .profile import WireSection
from .rawlog import FRAME, read_raw_log

#: Direction labels. Received frames are the game; sent frames are the
#: spectator's own chatter, kept because a raw log that drops half the
#: conversation cannot be replayed against a changed protocol.
RECEIVED = "recv"
SENT = "sent"


@dataclass(frozen=True, slots=True)
class RawFrame:
    """One text frame as it came off a socket, untouched."""

    socket: int
    """Which connection, indexed in order of appearance."""

    direction: str
    """:data:`RECEIVED` or :data:`SENT`."""

    at: float
    """Seconds since the source was created."""

    text: str


class FrameSource(Protocol):
    """An async iterator of :class:`RawFrame`, closable."""

    def __aiter__(self) -> AsyncIterator[RawFrame]: ...

    async def aclose(self) -> None: ...


class _Sentinel:
    """Marks the end of the queue; distinct from any frame."""


_CLOSED = _Sentinel()


class PlaywrightFrameSource:
    """Frames from the sockets a Playwright page opens.

    The page handler is registered **in the constructor**, not in a later
    ``start()``: the first socket opens during navigation, and a source
    attached afterwards misses the handshake — along with the join snapshot
    that is the only description of the table's opening state.
    """

    __slots__ = ("_wire", "_queue", "_started", "_sockets", "_closed", "_health")

    def __init__(
        self, page: Any, wire: WireSection, *, health: HealthLog | None = None
    ) -> None:
        """Attach to a page.

        Args:
            page: A Playwright page, or anything with the same ``on`` surface.
            wire: The profile's wire section, which knows which socket URLs
                belong to the game.
            health: The session's log, when the caller keeps one. The source
                counts through it rather than keeping a tally of its own: a
                shift opens one source per window, and these counters are the
                ones documented as never resetting.
        """

        self._wire = wire
        self._queue: asyncio.Queue[RawFrame | _Sentinel] = asyncio.Queue()
        self._started = time.perf_counter()
        self._sockets = 0
        self._closed = False
        self._health = health
        page.on("websocket", self._attach)

    def _attach(self, socket: Any) -> None:
        """Index a newly opened socket and listen to it, if it is ours."""

        if not self._wire.socket_url_pattern.search(socket.url):
            return
        index = self._sockets
        self._sockets += 1
        if self._health is not None:
            # A dropped connection is invisible in the frames themselves — the
            # mirror carries on — so every open and close is said out loud.
            self._health.counters.sockets_opened += 1
            self._health.event("socket_opened", socket=index)
        socket.on("framereceived", lambda payload: self._push(index, RECEIVED, payload))
        socket.on("framesent", lambda payload: self._push(index, SENT, payload))
        socket.on("close", lambda *_: self._closed_socket(index))

    def _closed_socket(self, index: int) -> None:
        """Count and log one socket closing. Runs inside Playwright's callback."""

        if self._health is not None:
            self._health.counters.sockets_closed += 1
            self._health.event("socket_closed", socket=index)

    def _push(self, socket: int, direction: str, payload: str | bytes) -> None:
        """Queue one frame. Runs inside Playwright's callback, so it must not
        block and must not raise."""

        # Binary frames are not part of this protocol; decoding rather than
        # dropping keeps the raw log a complete account of the connection.
        text = payload if isinstance(payload, str) else payload.decode(
            "utf-8", errors="replace"
        )
        self._queue.put_nowait(
            RawFrame(
                socket=socket,
                direction=direction,
                at=time.perf_counter() - self._started,
                text=text,
            )
        )

    def __aiter__(self) -> AsyncIterator[RawFrame]:
        return self

    async def __anext__(self) -> RawFrame:
        frame = await self._queue.get()
        if isinstance(frame, _Sentinel):
            # Put it back, so a second pass over a closed source stops too
            # rather than waiting on a queue nobody will fill again.
            self._queue.put_nowait(frame)
            raise StopAsyncIteration
        return frame

    async def aclose(self) -> None:
        """Stop iteration once the frames already queued have been drained."""

        if not self._closed:
            self._closed = True
            self._queue.put_nowait(_CLOSED)


class RawLogFrameSource:
    """Replays a raw log as the frames that wrote it.

    Interchangeable with :class:`PlaywrightFrameSource`, and that is the whole
    point: the pipeline downstream cannot tell a live session from a recorded
    one, so a game that parsed wrongly in production can be re-run offline as
    many times as the fix takes.
    """

    __slots__ = ("_frames", "_index", "_closed")

    def __init__(self, path: Path | str) -> None:
        """Read the log into memory.

        A session's log is tens of megabytes at most, and holding it lets the
        source be restarted and its length known — both of which a live
        socket cannot offer and a replay may as well.

        Args:
            path: The log file.

        Raises:
            WireError: If the log is unreadable.
        """

        self._frames = tuple(
            RawFrame(
                socket=line.socket or 0,
                direction=line.direction or RECEIVED,
                at=line.at or 0.0,
                text=line.text or "",
            )
            for line in read_raw_log(path)
            if line.kind == FRAME
        )
        self._index = 0
        self._closed = False

    def __aiter__(self) -> AsyncIterator[RawFrame]:
        return self

    async def __anext__(self) -> RawFrame:
        if self._closed or self._index >= len(self._frames):
            raise StopAsyncIteration
        frame = self._frames[self._index]
        self._index += 1
        return frame

    async def aclose(self) -> None:
        """Stop iteration. A replay holds no handles, so this only marks."""

        self._closed = True
