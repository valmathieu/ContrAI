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
from typing import Any, Protocol

from .profile import WireSection

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

    __slots__ = ("_wire", "_queue", "_started", "_sockets", "_closed")

    def __init__(self, page: Any, wire: WireSection) -> None:
        """Attach to a page.

        Args:
            page: A Playwright page, or anything with the same ``on`` surface.
            wire: The profile's wire section, which knows which socket URLs
                belong to the game.
        """

        self._wire = wire
        self._queue: asyncio.Queue[RawFrame | _Sentinel] = asyncio.Queue()
        self._started = time.perf_counter()
        self._sockets = 0
        self._closed = False
        page.on("websocket", self._attach)

    def _attach(self, socket: Any) -> None:
        """Index a newly opened socket and listen to it, if it is ours."""

        if not self._wire.socket_url_pattern.search(socket.url):
            return
        index = self._sockets
        self._sockets += 1
        socket.on("framereceived", lambda payload: self._push(index, RECEIVED, payload))
        socket.on("framesent", lambda payload: self._push(index, SENT, payload))

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
