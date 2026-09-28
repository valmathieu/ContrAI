"""Pins the health log: line shape, counters, heartbeat cadence."""

import json
from datetime import UTC, datetime

from contrai_scraper import HealthLog

#: The instant every line in this module is stamped with.
AT = datetime(2026, 9, 11, 18, 18, 15, tzinfo=UTC)


class TestLines:
    def test_an_event_is_one_json_object_per_line(self):
        lines: list[str] = []
        log = HealthLog(write=lines.append, clock=lambda: AT)
        log.event("table_rejected", reason="not_tournament", table="t1")
        assert json.loads(lines[0]) == {
            "at": "2026-09-11T18:18:15Z",
            "event": "table_rejected",
            "reason": "not_tournament",
            "table": "t1",
        }

    def test_a_heartbeat_carries_every_counter(self):
        lines: list[str] = []
        log = HealthLog(write=lines.append, clock=lambda: AT)
        log.counters.frames_received = 1167
        log.counters.frames_deduped = 577
        log.heartbeat(game="obs-g1")
        payload = json.loads(lines[0])
        assert (payload["event"], payload["frames_received"], payload["game"]) == (
            "heartbeat",
            1167,
            "obs-g1",
        )

    def test_every_counter_starts_at_zero(self):
        assert set(HealthLog(write=lambda _: None).counters.as_dict().values()) == {0}

    def test_a_heartbeat_carries_the_socket_counters(self):
        # A dropped connection is invisible in the frames themselves — the
        # mirror carries on — so the beat is where a reconnect shows up.
        lines: list[str] = []
        log = HealthLog(write=lines.append, clock=lambda: AT)
        log.counters.sockets_opened = 3
        log.counters.sockets_closed = 2
        log.heartbeat()
        payload = json.loads(lines[0])
        assert (payload["sockets_opened"], payload["sockets_closed"]) == (3, 2)


class TestCadence:
    def test_a_heartbeat_is_not_due_before_the_interval(self):
        ticks = iter([0.0, 59.0, 61.0])
        log = HealthLog(write=lambda _: None, clock=lambda: AT,
                        monotonic=lambda: next(ticks))
        assert (log.due(60.0), log.due(60.0)) == (False, True)

    def test_a_heartbeat_resets_the_interval(self):
        # Without the reset a due heartbeat stays due, and one slow round
        # turns the health log into a flood.
        ticks = iter([0.0, 61.0, 61.0, 62.0])
        log = HealthLog(write=lambda _: None, clock=lambda: AT,
                        monotonic=lambda: next(ticks))
        assert log.due(60.0)
        log.heartbeat()
        assert not log.due(60.0)


class TestStream:
    def test_the_default_stream_is_stderr(self, capsys):
        # Telemetry must not mix with a subcommand's stdout: `parse` prints
        # the records it wrote, and a caller pipes that somewhere.
        HealthLog(clock=lambda: AT).event("seated", table="t1")
        captured = capsys.readouterr()
        assert (captured.out, json.loads(captured.err)["event"]) == ("", "seated")

    def test_the_default_clock_stamps_the_line(self, capsys):
        HealthLog().event("seated")
        stamp = json.loads(capsys.readouterr().err)["at"]
        assert datetime.fromisoformat(stamp).tzinfo is not None


class TestWorkers:
    def test_a_workers_line_carries_its_label_after_the_event(self):
        lines: list[str] = []
        fleet = HealthLog(write=lines.append, clock=lambda: AT)
        fleet.worker("bot01").event("table_seated", table="t1")
        payload = json.loads(lines[0])
        assert list(payload) == ["at", "event", "worker", "table"]
        assert payload["worker"] == "bot01"

    def test_a_workers_heartbeat_carries_its_label_and_its_own_counts(self):
        lines: list[str] = []
        fleet = HealthLog(write=lines.append, clock=lambda: AT)
        first, second = fleet.worker("bot01"), fleet.worker("bot02")
        first.counters.tables_seated = 3
        second.counters.tables_seated = 5
        second.heartbeat()
        payload = json.loads(lines[0])
        assert (payload["worker"], payload["tables_seated"]) == ("bot02", 5)

    def test_the_fleet_heartbeat_adds_every_worker_up(self):
        lines: list[str] = []
        fleet = HealthLog(write=lines.append, clock=lambda: AT)
        for label, seated in (("bot01", 3), ("bot02", 5)):
            fleet.worker(label).counters.tables_seated = seated
        fleet.fleet_heartbeat()
        payload = json.loads(lines[0])
        assert (payload["event"], payload["workers"], payload["tables_seated"],
                "worker" in payload) == ("fleet_heartbeat", 2, 8, False)

    def test_an_unlabelled_log_writes_what_it_always_wrote(self):
        # `run` has one worker and no label; its lines must not change shape.
        lines: list[str] = []
        HealthLog(write=lines.append, clock=lambda: AT).event("seated", table="t1")
        assert "worker" not in json.loads(lines[0])

    def test_each_worker_beats_on_its_own_interval(self):
        # One worker's heartbeat must not reset another's, or a busy worker
        # would silence a quiet one.
        now = [0.0]
        fleet = HealthLog(write=lambda _: None, clock=lambda: AT,
                          monotonic=lambda: now[0])
        first, second = fleet.worker("bot01"), fleet.worker("bot02")
        now[0] = 61.0
        first.heartbeat()
        assert (first.due(60.0), second.due(60.0)) == (False, True)

    def test_the_fleet_heartbeat_resets_the_fleets_interval(self):
        now = [0.0]
        fleet = HealthLog(write=lambda _: None, clock=lambda: AT,
                          monotonic=lambda: now[0])
        now[0] = 61.0
        fleet.fleet_heartbeat()
        assert fleet.due(60.0) is False
