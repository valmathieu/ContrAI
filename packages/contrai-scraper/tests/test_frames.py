"""Pins the frame source: socket indexing, queueing, close, socket counting."""

import asyncio
import json

from contrai_scraper import HealthLog, PlaywrightFrameSource


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


class TestSocketCounting:
    def test_an_opened_socket_is_counted_and_logged(self, profile):
        async def scenario():
            lines: list[str] = []
            health = HealthLog(write=lines.append)
            page = FakePage()
            PlaywrightFrameSource(page, profile.wire, health=health)
            page.open_socket("wss://example.invalid/sock/1")
            return health.counters.sockets_opened, [
                json.loads(line)["event"] for line in lines
            ]

        assert drain(scenario) == (1, ["socket_opened"])

    def test_a_closed_socket_is_counted_and_names_its_index(self, profile):
        async def scenario():
            lines: list[str] = []
            health = HealthLog(write=lines.append)
            page = FakePage()
            PlaywrightFrameSource(page, profile.wire, health=health)
            page.open_socket("wss://example.invalid/sock/1")
            second = page.open_socket("wss://example.invalid/sock/2")
            second.emit("close", second)
            closed = [
                json.loads(line)
                for line in lines
                if json.loads(line)["event"] == "socket_closed"
            ]
            return health.counters.sockets_closed, [line["socket"] for line in closed]

        assert drain(scenario) == (1, [1])

    def test_a_socket_that_is_not_ours_is_not_counted(self, profile):
        # A page opens sockets for chat, telemetry and whatever else the site
        # runs; only the game's connections say anything about the recording.
        async def scenario():
            health = HealthLog(write=lambda _: None)
            page = FakePage()
            PlaywrightFrameSource(page, profile.wire, health=health)
            other = page.open_socket("wss://elsewhere.invalid/other")
            other.emit("close", other)
            return health.counters.sockets_opened, health.counters.sockets_closed

        assert drain(scenario) == (0, 0)

    def test_without_a_health_log_nothing_is_counted(self, profile):
        # The replay path and the profile check build a source with no log.
        async def scenario():
            page = FakePage()
            PlaywrightFrameSource(page, profile.wire)
            socket = page.open_socket("wss://example.invalid/sock/1")
            socket.emit("close", socket)
            return "no counting, no crash"

        assert drain(scenario) == "no counting, no crash"
