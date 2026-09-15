"""Pins the shift: the schedule gate, the egress gate, sessions and budgets."""

import asyncio
import dataclasses
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contrai_scraper import (
    EGRESS_BUDGET,
    FAILURE_BUDGET,
    BrowserError,
    EgressReading,
    EgressRefusal,
    HealthLog,
    RawLogWriter,
    RecorderLimits,
    SessionSummary,
    Shift,
    ShiftError,
    ShiftSummary,
    StopReason,
    parse_range,
    raw_path,
)
from contrai_scraper.shift import _utc_now

OPEN = EgressReading(refusal=None, exit_ip="203.0.113.7", country="XX", route_device="tun0")
BLOCKED = EgressReading(refusal=EgressRefusal.PROBE_FAILED, exit_ip=None, country=None,
                        route_device=None)
HOME = EgressReading(refusal=EgressRefusal.EXIT_IS_HOME, exit_ip=None, country="XX",
                     route_device=None)

#: Noon in UTC, inside the fixture profile's always-open window.
NOON = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

#: Seconds in a day, for backdating a raw log.
DAY = 86_400


class FakeTime:
    """A wall clock and a monotonic clock that only move when told to, together."""

    def __init__(self, start: datetime):
        self.wall = start
        self.mono = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.advance(seconds)


class FakeEgress:
    """Answers checks from a script, repeating its last reading."""

    def __init__(self, *readings):
        self._readings = list(readings)
        self.calls = 0

    async def check(self):
        self.calls += 1
        return self._readings[min(self.calls, len(self._readings)) - 1]


class FakeSpectator:
    """The browser half of a session, which may refuse to walk.

    ``captures`` is shared with the test so a capture taken after the
    context manager has closed is still visible to it.
    """

    def __init__(self, *, fail=None, captures=None):
        self._fail = fail
        self.captures = captures if captures is not None else []

    async def log_in(self):
        pass

    async def enter_variant(self):
        if self._fail is not None:
            raise self._fail

    async def capture(self, stem):
        self.captures.append(stem)
        return (stem.with_suffix(".png"), stem.with_suffix(".html"))


def opener(opened, *, error=None, walk_error=None, captures=None):
    """An ``open_spectator`` stand-in recording the headless flag it was given.

    ``error`` fails before the browser is yielded, as a launch does;
    ``walk_error`` fails inside the session, which is the only shape that
    leaves a page to photograph.
    """

    @asynccontextmanager
    async def open_session(profile, *, headless, health):
        opened.append(headless)
        if error is not None:
            raise error
        yield FakeSpectator(fail=walk_error, captures=captures), object()

    return open_session


class Recorders:
    """Builds fake recorders returning scripted summaries; remembers what each was given."""

    def __init__(self, time, *summaries, spend=0.0):
        self._time = time
        self._summaries = list(summaries)
        self._spend = spend
        self.limits: list[RecorderLimits] = []
        self.raws: list = []

    def __call__(self, spectator, frames, profile, health, *, limits, raw, egress):
        self.limits.append(limits)
        self.raws.append(raw.path)
        summary = self._summaries.pop(0) if self._summaries else done(0)
        time, spend = self._time, self._spend

        class _Recorder:
            async def run(self):
                time.advance(spend)
                return summary

        return _Recorder()


def done(games, reason=StopReason.WINDOW_CLOSED):
    return SessionSummary(games_recorded=games, tables_seated=games, tables_rejected=0,
                          records=(), stop_reason=reason)


def windowed(profile, *ranges, finish=True):
    """The fixture profile under a schedule of its own."""

    schedule = dataclasses.replace(profile.schedule, active=tuple(map(parse_range, ranges)),
                                   finish_current_game=finish)
    return dataclasses.replace(profile, schedule=schedule)


def events(lines):
    return [json.loads(line)["event"] for line in lines]


def shift(profile, time, *, egress=None, recorders=None, opened=None, error=None,
          lines=None, walk_error=None, captures=None, **kwargs):
    return Shift(
        profile,
        HealthLog(write=(lines if lines is not None else []).append, clock=time.now,
                  monotonic=time.monotonic),
        open_session=opener(opened if opened is not None else [], error=error,
                            walk_error=walk_error, captures=captures),
        egress=egress or FakeEgress(OPEN),
        clock=time.now, monotonic=time.monotonic, sleep=time.sleep,
        recorder=recorders or Recorders(time), **kwargs,
    )


class TestSchedule:
    def test_outside_the_window_no_browser_is_opened(self, profile):
        # 01:00Z is 03:00 in Paris: the 08:00-23:30 window opens at 06:00Z.
        time = FakeTime(datetime(2026, 9, 14, 1, 0, tzinfo=UTC))
        opened, lines = [], []
        asyncio.run(shift(windowed(profile, "08:00-23:30"), time, opened=opened, lines=lines,
                          limits=RecorderLimits(max_seconds=600)).run())
        idle = [json.loads(line) for line in lines if '"schedule_idle"' in line]
        assert (opened, time.slept, len(idle), idle[0]["next_opening"]) == (
            [], [300, 300], 1, "2026-09-14T06:00:00Z"
        )

    def test_an_opening_window_logs_a_resume_then_a_session(self, profile):
        # 05:55Z is 07:55 in Paris: one poll later the window has opened.
        time = FakeTime(datetime(2026, 9, 14, 5, 55, tzinfo=UTC))
        lines: list[str] = []
        asyncio.run(shift(windowed(profile, "08:00-23:30"), time, lines=lines,
                          recorders=Recorders(time, done(1)),
                          limits=RecorderLimits(max_games=1)).run())
        wanted = {"schedule_idle", "schedule_resume", "egress_ok", "session_started"}
        assert [name for name in events(lines) if name in wanted] == [
            "schedule_idle", "schedule_resume", "egress_ok", "session_started"
        ]

    def test_the_recorder_is_told_when_the_window_closes(self, profile):
        # 21:00Z is 23:00 in Paris: half an hour to the close, then the
        # profile's thirty minutes of overrun for the game in hand.
        time = FakeTime(datetime(2026, 9, 14, 21, 0, tzinfo=UTC))
        recorders = Recorders(time, done(1))
        asyncio.run(shift(windowed(profile, "08:00-23:30"), time, recorders=recorders,
                          limits=RecorderLimits(max_games=1)).run())
        assert (recorders.limits[0].seat_until_s, recorders.limits[0].max_seconds) == (
            1800.0, 3600.0
        )

    def test_without_finishing_the_game_the_close_is_the_hard_stop(self, profile):
        time = FakeTime(datetime(2026, 9, 14, 21, 0, tzinfo=UTC))
        recorders = Recorders(time, done(1))
        asyncio.run(shift(windowed(profile, "08:00-23:30", finish=False), time,
                          recorders=recorders, limits=RecorderLimits(max_games=1)).run())
        assert recorders.limits[0].max_seconds == 1800.0

    def test_an_always_open_window_leaves_the_recorder_unbounded(self, profile):
        time = FakeTime(NOON)
        recorders = Recorders(time, done(1))
        asyncio.run(shift(profile, time, recorders=recorders,
                          limits=RecorderLimits(max_games=1)).run())
        assert (recorders.limits[0].seat_until_s, recorders.limits[0].max_seconds) == (
            None, None
        )

    def test_the_run_time_limit_caps_both_deadlines(self, profile):
        time = FakeTime(NOON)
        recorders = Recorders(time, spend=120)
        summary = asyncio.run(shift(profile, time, recorders=recorders,
                                    limits=RecorderLimits(max_seconds=120)).run())
        assert (recorders.limits[0].seat_until_s, recorders.limits[0].max_seconds,
                summary.sessions) == (120.0, 120.0, 1)

    def test_the_default_clock_reads_utc(self):
        # The box may run in any local zone; a shift reasons in real instants.
        assert _utc_now().tzinfo is UTC


class TestEgress:
    def test_a_refused_egress_opens_no_browser(self, profile):
        time = FakeTime(NOON)
        opened, lines = [], []
        asyncio.run(shift(profile, time, egress=FakeEgress(BLOCKED), opened=opened,
                          lines=lines, limits=RecorderLimits(max_seconds=600)).run())
        assert (opened, events(lines).count("egress_blocked")) == ([], 2)

    def test_the_egress_budget_ends_the_shift(self, profile):
        time = FakeTime(NOON)
        with pytest.raises(ShiftError):
            asyncio.run(shift(profile, time, egress=FakeEgress(BLOCKED)).run())
        assert len(time.slept) == EGRESS_BUDGET - 1

    def test_a_recovered_egress_resets_the_budget(self, profile):
        # A tunnel that flaps is not a tunnel that is down: only refusals in
        # a row spend the budget.
        time = FakeTime(NOON)
        readings = [*[BLOCKED] * (EGRESS_BUDGET - 1), OPEN,
                    *[BLOCKED] * (EGRESS_BUDGET - 1), OPEN]
        recorders = Recorders(time, done(0, StopReason.EGRESS_BLOCKED), done(1))
        summary = asyncio.run(shift(profile, time, egress=FakeEgress(*readings),
                                    recorders=recorders,
                                    limits=RecorderLimits(max_games=1)).run())
        assert summary.games_recorded == 1

    def test_no_line_ever_carries_the_home_address(self, profile):
        time = FakeTime(NOON)
        lines: list[str] = []
        asyncio.run(shift(profile, time, egress=FakeEgress(HOME, HOME, OPEN), lines=lines,
                          recorders=Recorders(time, done(1)),
                          limits=RecorderLimits(max_games=1)).run())
        assert [line for line in lines if profile.egress.home_ip in line] == []


class TestSessions:
    def test_every_session_keeps_its_own_raw_log(self, profile):
        time = FakeTime(NOON)
        recorders = Recorders(time, done(0, StopReason.EGRESS_BLOCKED), done(1))
        asyncio.run(shift(profile, time, recorders=recorders,
                          limits=RecorderLimits(max_games=1)).run())
        assert (len(set(recorders.raws)), all(path.exists() for path in recorders.raws)) == (
            2, True
        )

    def test_max_games_spans_sessions(self, profile):
        time = FakeTime(NOON)
        recorders = Recorders(time, done(1), done(1))
        asyncio.run(shift(profile, time, recorders=recorders,
                          limits=RecorderLimits(max_games=2)).run())
        assert [limits.max_games for limits in recorders.limits] == [2, 1]

    def test_a_failing_session_is_retried_then_the_shift_ends(self, profile):
        time = FakeTime(NOON)
        lines: list[str] = []
        with pytest.raises(ShiftError):
            asyncio.run(shift(profile, time, lines=lines,
                              error=BrowserError("[selectors].variant matched nothing")).run())
        assert (events(lines).count("session_failed"), len(time.slept)) == (
            FAILURE_BUDGET, FAILURE_BUDGET - 1
        )

    def test_a_browser_failure_photographs_the_page_beside_its_raw_log(self, profile):
        # A step that fails names the profile key it was on and nothing else,
        # and the browser is shut by the time the caller reads the error. The
        # image and the DOM are the only account of what the page looked like.
        time = FakeTime(NOON)
        asking = dataclasses.replace(
            profile, browser=dataclasses.replace(profile.browser, screenshot_on_error=True)
        )
        captures, lines = [], []
        with pytest.raises(ShiftError):
            asyncio.run(shift(asking, time, lines=lines, captures=captures,
                              walk_error=BrowserError("[selectors].options_button")).run())
        assert len(captures) == FAILURE_BUDGET
        assert all(stem.suffix == "" for stem in captures)
        assert events(lines).count("failure_captured") == FAILURE_BUDGET

    def test_the_photograph_shares_the_stem_of_the_session_it_failed_in(self, profile):
        time = FakeTime(NOON)
        asking = dataclasses.replace(
            profile, browser=dataclasses.replace(profile.browser, screenshot_on_error=True)
        )
        captures, lines = [], []
        with pytest.raises(ShiftError):
            asyncio.run(shift(asking, time, lines=lines, captures=captures,
                              walk_error=BrowserError("[selectors].options_button")).run())
        written = [json.loads(line) for line in lines]
        files = next(e for e in written if e["event"] == "failure_captured")["files"]
        assert [Path(name).stem for name in files] == [captures[0].name] * 2
        assert sorted(Path(name).suffix for name in files) == [".html", ".png"]

    def test_a_profile_that_asks_for_no_photograph_takes_none(self, profile):
        # The fixture profile leaves it off, which is the shipped default.
        time = FakeTime(NOON)
        captures, lines = [], []
        with pytest.raises(ShiftError):
            asyncio.run(shift(profile, time, lines=lines, captures=captures,
                              walk_error=BrowserError("[selectors].options_button")).run())
        assert captures == []
        assert "failure_captured" not in events(lines)

    def test_a_session_that_never_opened_is_not_photographed(self, profile):
        # A launch that fails yields no page, so there is nothing to ask.
        time = FakeTime(NOON)
        asking = dataclasses.replace(
            profile, browser=dataclasses.replace(profile.browser, screenshot_on_error=True)
        )
        lines: list[str] = []
        with pytest.raises(ShiftError):
            asyncio.run(shift(asking, time, lines=lines,
                              error=BrowserError("[selectors].login_start")).run())
        assert "failure_captured" not in events(lines)

    def test_a_session_whose_frames_ended_counts_as_a_failure(self, profile):
        # A live frame source only ends when its browser is gone.
        time = FakeTime(NOON)
        recorders = Recorders(time, *[done(0, StopReason.SOURCE_ENDED)] * FAILURE_BUDGET)
        with pytest.raises(ShiftError, match="frame source ended"):
            asyncio.run(shift(profile, time, recorders=recorders).run())

    def test_a_good_session_resets_the_failure_count(self, profile):
        time = FakeTime(NOON)
        ended = done(0, StopReason.SOURCE_ENDED)
        recorders = Recorders(time, *[ended] * (FAILURE_BUDGET - 1), done(0),
                              *[ended] * (FAILURE_BUDGET - 1), done(1))
        summary = asyncio.run(shift(profile, time, recorders=recorders,
                                    limits=RecorderLimits(max_games=1)).run())
        assert summary.sessions == 2 * FAILURE_BUDGET

    def test_old_raw_logs_are_pruned_before_a_session_opens(self, profile):
        time = FakeTime(NOON)
        old = raw_path(profile.output.raw_root, "old")
        RawLogWriter(old).close()
        stamp = NOON.timestamp() - 40 * DAY
        os.utime(old, (stamp, stamp))
        lines: list[str] = []
        asyncio.run(shift(profile, time, lines=lines, recorders=Recorders(time, done(1)),
                          limits=RecorderLimits(max_games=1)).run())
        pruned = [json.loads(line) for line in lines if '"raw_logs_pruned"' in line]
        assert (old.exists(), [line["count"] for line in pruned]) == (False, [1])

    def test_the_session_line_names_the_effective_headless_flag(self, profile):
        # The opener is handed the override, which is None here; the line
        # says what the browser will actually do, which is the profile's.
        time = FakeTime(NOON)
        opened, lines = [], []
        asyncio.run(shift(profile, time, opened=opened, lines=lines,
                          recorders=Recorders(time, done(1)),
                          limits=RecorderLimits(max_games=1)).run())
        started = next(json.loads(line) for line in lines if '"session_started"' in line)
        assert (started["headless"], opened) == (True, [None])

    def test_the_summary_adds_up_every_session(self, profile):
        time = FakeTime(NOON)
        summary = asyncio.run(shift(profile, time, recorders=Recorders(time, done(1), done(1)),
                                    limits=RecorderLimits(max_games=2)).run())
        assert summary == ShiftSummary(sessions=2, games_recorded=2, tables_seated=2,
                                       tables_rejected=0, records=())
