"""Pins the schedule: ranges, midnight, daylight saving, the next change."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from contrai_scraper import ActiveRange, ProfileError, Schedule, parse_range, timezone_named

PARIS = ZoneInfo("Europe/Paris")


def schedule(*ranges: str) -> Schedule:
    return Schedule(timezone=PARIS, active=tuple(map(parse_range, ranges)),
                    finish_current_game=True, max_overrun_minutes=30,
                    idle_poll_minutes=5)


def paris(*args: int) -> datetime:
    return datetime(*args, tzinfo=PARIS)


class TestParsing:
    def test_a_range_reads_as_minutes_after_midnight(self):
        assert parse_range("08:00-23:30") == ActiveRange(start=480, end=1410)

    def test_the_end_of_the_day_is_spelled_24_00(self):
        assert parse_range("00:00-24:00") == ActiveRange(start=0, end=1440)

    @pytest.mark.parametrize("text", ["8:00-23:30", "08:00", "25:00-26:00", "08:60-09:00",
                                      "08:00-24:01", "24:00-08:00", "08:00-08:00", "x"])
    def test_a_malformed_or_empty_range_is_refused(self, text):
        with pytest.raises(ProfileError, match=text):
            parse_range(text)

    def test_an_unknown_timezone_is_refused(self):
        with pytest.raises(ProfileError, match="Mars/Olympus"):
            timezone_named("Mars/Olympus")


class TestActive:
    def test_inside_a_daytime_range(self):
        assert schedule("08:00-23:30").is_active(paris(2026, 9, 14, 12, 0)) is True

    def test_the_end_minute_is_already_outside(self):
        window = schedule("08:00-23:30")
        assert (window.is_active(paris(2026, 9, 14, 23, 30)),
                window.is_active(paris(2026, 9, 14, 23, 29))) == (False, True)

    def test_a_range_across_midnight_covers_both_sides(self):
        window = schedule("22:00-02:00")
        assert (window.is_active(paris(2026, 9, 14, 23, 0)),
                window.is_active(paris(2026, 9, 14, 1, 59)),
                window.is_active(paris(2026, 9, 14, 2, 0)),
                window.is_active(paris(2026, 9, 14, 12, 0))) == (True, True, False, False)

    def test_the_range_is_read_in_the_profile_timezone_not_utc(self):
        # 06:30Z is 08:30 in Paris: read as UTC the window would still be shut.
        assert schedule("08:00-23:30").is_active(
            datetime(2026, 9, 14, 6, 30, tzinfo=UTC)
        ) is True

    def test_any_of_several_ranges_is_enough(self):
        window = schedule("06:00-09:00", "18:00-23:00")
        assert (window.is_active(paris(2026, 9, 14, 19, 0)),
                window.is_active(paris(2026, 9, 14, 12, 0))) == (True, False)


class TestNextChange:
    def test_a_closed_window_names_its_next_opening(self):
        now = paris(2026, 9, 14, 3, 0)
        assert schedule("08:00-23:30").next_change(now) == datetime(2026, 9, 14, 6, 0, tzinfo=UTC)

    def test_an_open_window_names_its_close(self):
        assert schedule("08:00-23:30").next_change(paris(2026, 9, 14, 23, 0)) == datetime(
            2026, 9, 14, 21, 30, tzinfo=UTC
        )

    def test_an_always_open_window_never_changes(self):
        assert schedule("00:00-24:00").next_change(paris(2026, 9, 14, 12, 0)) is None

    def test_seconds_do_not_push_the_change_a_minute_late(self):
        # A shift sleeps until this instant; a stray half-minute would wake it
        # after the close rather than at it.
        now = paris(2026, 9, 14, 23, 29, 30)
        assert schedule("08:00-23:30").next_change(now) == datetime(
            2026, 9, 14, 21, 30, tzinfo=UTC
        )

    def test_the_spring_forward_day_closes_an_hour_early_in_utc(self):
        # 2026-03-29: the clocks go 02:00 -> 03:00, so the local window
        # "01:00-04:00" lasts two hours of real time, not three.
        window = schedule("01:00-04:00")
        assert (window.next_change(datetime(2026, 3, 28, 23, 30, tzinfo=UTC)),
                window.next_change(datetime(2026, 3, 29, 0, 30, tzinfo=UTC))) == (
            datetime(2026, 3, 29, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 2, 0, tzinfo=UTC),
        )

    def test_the_fall_back_day_keeps_the_window_open_through_the_repeat(self):
        # 2026-10-25: 03:00 CEST becomes 02:00 CET, so the same local window
        # lasts four hours of real time.
        assert schedule("01:00-04:00").next_change(
            datetime(2026, 10, 24, 23, 30, tzinfo=UTC)
        ) == datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
