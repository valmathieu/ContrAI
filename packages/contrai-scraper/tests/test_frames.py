"""Pins the frame source: socket indexing, queueing, close."""

import asyncio

from contrai_scraper import PlaywrightFrameSource


class FakeSocket:
    def __init__(self, url: str) -> None:
        self.url = url
        self._handlers: dict[str, list] = {}

    def on(self, name: str, handler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    def emit(self, name: str, payload) -> None:
        for handler in self._handlers.get(name, []):
            handler(payload)


class FakePage:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def on(self, name: str, handler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    def open_socket(self, url: str) -> FakeSocket:
        socket = FakeSocket(url)
        for handler in self._handlers.get("websocket", []):
            handler(socket)
        return socket


def drain(page_script) -> list:
    """Runs an async scenario and returns what it collected.

    ``pytest-asyncio`` is **not** a dependency and must not become one: the
    scraper's only async surface is this bridge, and one ``asyncio.run`` per
    test covers it without adding a plugin to every package's install.
    """

    return asyncio.run(page_script())


class TestPlaywrightFrameSource:
    def test_frames_arrive_in_order(self, profile):
        async def scenario():
            page = FakePage()
            source = PlaywrightFrameSource(page, profile.wire)
            socket = page.open_socket("wss://example.invalid/sock/1")
            socket.emit("framereceived", "one")
            socket.emit("framereceived", "two")
            await source.aclose()
            return [frame.text async for frame in source]

        assert drain(scenario) == ["one", "two"]

    def test_each_matching_socket_gets_a_stable_index(self, profile):
        async def scenario():
            page = FakePage()
            source = PlaywrightFrameSource(page, profile.wire)
            first = page.open_socket("wss://example.invalid/sock/1")
            second = page.open_socket("wss://example.invalid/sock/2")
            first.emit("framereceived", "a")
            second.emit("framereceived", "b")
            first.emit("framereceived", "c")
            await source.aclose()
            return [(f.socket, f.text) async for f in source]

        assert drain(scenario) == [(0, "a"), (1, "b"), (0, "c")]

    def test_a_socket_that_does_not_match_the_pattern_is_ignored(self, profile):
        async def scenario():
            page = FakePage()
            source = PlaywrightFrameSource(page, profile.wire)
            page.open_socket("wss://elsewhere.invalid/other").emit("framereceived", "noise")
            await source.aclose()
            return [f async for f in source]

        assert drain(scenario) == []

    def test_sent_frames_are_labelled(self, profile):
        async def scenario():
            page = FakePage()
            source = PlaywrightFrameSource(page, profile.wire)
            page.open_socket("wss://example.invalid/sock/1").emit("framesent", "out")
            await source.aclose()
            return [(f.direction, f.text) async for f in source]

        assert drain(scenario) == [("sent", "out")]

    def test_closing_twice_leaves_the_source_iterable(self, profile):
        # The recorder closes on its own way out and again in a finally, so a
        # second close must not enqueue a second sentinel — one left behind
        # would end the *next* pass early.
        async def scenario():
            page = FakePage()
            source = PlaywrightFrameSource(page, profile.wire)
            page.open_socket("wss://example.invalid/sock/1").emit("framereceived", "one")
            await source.aclose()
            await source.aclose()
            first = [f.text async for f in source]
            second = [f.text async for f in source]
            return first, second

        assert drain(scenario) == (["one"], [])

    def test_a_source_attached_before_navigation_sees_the_first_socket(self, profile):
        # Attaching after navigation misses the handshake, which is why the
        # constructor registers the page handler rather than a start() call.
        async def scenario():
            page = FakePage()
            source = PlaywrightFrameSource(page, profile.wire)
            page.open_socket("wss://example.invalid/sock/1").emit("framereceived", "first")
            await source.aclose()
            return [f.text async for f in source]

        assert drain(scenario) == ["first"]
