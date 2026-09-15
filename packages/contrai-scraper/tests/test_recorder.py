"""Pins the recorder: the gates, the boundary read, the hop, the watchdog."""

import asyncio
import copy
import itertools
import json

import pytest
from contrai_core import Position, Suit, TeamSide
from contrai_data import EndReason, load_game

from contrai_scraper import (
    EgressReading,
    EgressRefusal,
    HealthLog,
    OptionsReading,
    RawFrame,
    RawLogWriter,
    Recorder,
    RecorderLimits,
    ScoreboardReading,
    StopReason,
    read_raw_log,
)

#: A working tunnel, and one that is not.
OPEN = EgressReading(refusal=None, exit_ip="203.0.113.7", country="XX", route_device="tun0")
BLOCKED = EgressReading(refusal=EgressRefusal.PROBE_FAILED, exit_ip=None, country=None,
                        route_device=None)


class FakeEgress:
    """Answers every check with one reading, and counts the checks."""

    def __init__(self, reading):
        self.reading = reading
        self.calls = 0

    async def check(self):
        self.calls += 1
        return self.reading


class FakeSpectator:
    """Answers the recorder's browser calls from canned readings."""

    def __init__(self, *, options=None, scoreboard=None, resume=True,
                 on_resume=None):
        self.calls: list[tuple] = []
        self._options = options if options is not None else _matching_options()
        self._scoreboard = (
            scoreboard if scoreboard is not None else ScoreboardReading((), "")
        )
        self._resume = resume
        self._on_resume = on_resume

    async def read_options(self, expected):
        self.calls.append(("read_options",))
        return self._options

    async def read_scoreboard(self):
        self.calls.append(("read_scoreboard",))
        return self._scoreboard

    async def request_state(self, table_id, last_event_id):
        self.calls.append(("request_state", table_id, last_event_id))
        if self._on_resume is not None:
            self._on_resume()
        return self._resume

    async def next_table(self):
        self.calls.append(("next_table",))


class FakeFrameSource:
    """A scripted frame source; a callable entry runs before the next frame."""

    def __init__(self, script):
        self._script = list(script)

    def __aiter__(self):
        return self

    async def __anext__(self):
        while self._script:
            item = self._script.pop(0)
            if callable(item):
                item()
                continue
            return item
        raise StopAsyncIteration

    async def aclose(self):
        pass


class StallingFrameSource(FakeFrameSource):
    """A scripted source that then goes quiet, the way a dead table does.

    The watchdog's real trigger is a wait that expires, not a source that
    ends: a table nobody is playing at keeps its socket open.
    """

    def __init__(self, script, stalls=1):
        super().__init__(script)
        self._stalls = stalls

    async def __anext__(self):
        if self._script:
            return await super().__anext__()
        if self._stalls:
            self._stalls -= 1
            await asyncio.sleep(60)
        raise StopAsyncIteration


def _matching_options():
    """An options panel that agrees with the fixture profile."""

    return OptionsReading(
        observed={"opt_alpha": True, "opt_beta": False},
        missing=(),
        extra=(),
        differing=(),
    )


def frame(text, socket=0, at=0.0):
    """One received frame."""

    return RawFrame(socket=socket, direction="recv", at=at, text=text)


def snapshot_frame(builders, *, frame_id="s0", tournament=True, account="100",
                   **kwargs):
    """A join snapshot, with the two things the gates read made adjustable."""

    payload = copy.deepcopy(builders.snapshot_payload(**kwargs))
    payload["table"]["cup"] = tournament
    for index, block in enumerate(payload["state"]["people"].values(), start=1):
        block["acct"] = f"{account}{index}"
    return frame(
        builders.envelope("payload", "joinTable", payload, frame_id=frame_id)
    )


def run_recorder(spectator, script, profile, health=None, source=None, **kwargs):
    """Drive one recorder over a scripted source and return its summary."""

    recorder = Recorder(
        spectator,
        FakeFrameSource(script) if source is None else source,
        profile,
        health if health is not None else HealthLog(write=lambda _: None),
        **kwargs,
    )
    return asyncio.run(recorder.run())


def records_in(profile):
    """Every record the recorder wrote under the profile's root."""

    return [load_game(path) for path in sorted((profile.output.root / "games").glob("*.jsonl"))]


class TestGates:
    def test_a_non_tournament_table_is_rejected_without_touching_the_dom(
        self, profile, builders
    ):
        # The snapshot's own flag agreed with the rendered marker on 23 of 23
        # visits, 7 of them negative. Rejecting from the wire costs nothing.
        spectator = FakeSpectator()
        run_recorder(spectator, [snapshot_frame(builders, tournament=False)],
                     profile)
        assert ("read_options",) not in spectator.calls
        assert spectator.calls == [("next_table",)]

    def test_a_table_too_far_along_is_rejected(self, profile, builders):
        spectator = FakeSpectator()
        script = [snapshot_frame(builders, rows=[builders.score_row()] * 8,
                                 round_index=8)]
        summary = run_recorder(spectator, script, profile)
        assert (spectator.calls, summary.tables_rejected) == (
            [("next_table",)], 1
        )

    def test_a_table_this_profile_cannot_read_is_left_rather_than_fatal(
        self, profile, builders
    ):
        # The site runs variants whose contracts the tournament ruleset has no
        # name for — all trump is the one that was met live. The options gate
        # that refuses such a table runs *after* the snapshot is read, so an
        # unreadable row would otherwise end the session and spend a slot of
        # the shift's failure budget rather than costing one hop.
        spectator = FakeSpectator()
        script = [snapshot_frame(builders,
                                 rows=[builders.score_row(suit="everything")])]
        summary = run_recorder(spectator, script, profile)
        assert (spectator.calls, summary.tables_rejected) == (
            [("next_table",)], 1
        )

    def test_an_unreadable_table_logs_what_it_could_not_read(
        self, profile, builders
    ):
        # Hopping must not hide a profile that has genuinely drifted, so the
        # refused token travels into the health line.
        lines: list[str] = []
        health = HealthLog(write=lines.append)
        run_recorder(FakeSpectator(),
                     [snapshot_frame(builders,
                                     rows=[builders.score_row(suit="everything")])],
                     profile, health=health)
        rejected = [json.loads(line) for line in lines
                    if json.loads(line)["event"] == "table_rejected"]
        assert [entry["reason"] for entry in rejected] == ["unreadable_snapshot"]
        assert "everything" in rejected[0]["error"]
        assert health.counters.tables_rejected == 1

    def test_an_unreadable_table_is_followed_by_a_readable_one(
        self, profile, builders
    ):
        # The hop has to leave the loop able to seat: a rejected table resets
        # the buffer the way every other rejection does.
        spectator = FakeSpectator()
        script = [
            snapshot_frame(builders, frame_id="s0",
                           rows=[builders.score_row(suit="everything")]),
            snapshot_frame(builders, frame_id="s1", table_id="t2"),
        ]
        summary = run_recorder(spectator, script, profile)
        assert (summary.tables_rejected, summary.tables_seated) == (1, 1)

    def test_an_options_mismatch_aborts_the_table(self, profile, builders):
        spectator = FakeSpectator(
            options=OptionsReading(observed={"opt_alpha": True},
                                   missing=("opt_beta",), extra=(),
                                   differing=())
        )
        summary = run_recorder(spectator, [snapshot_frame(builders)], profile)
        assert (spectator.calls[-1], summary.tables_seated) == (
            ("next_table",), 0
        )

    def test_an_orientation_mismatch_aborts_the_table(self, profile, builders):
        # Recording a table with guessed sides produces a record that is
        # well-formed and wrong about who won every round.
        spectator = FakeSpectator(
            scoreboard=ScoreboardReading(rows=((0, 80),), text="0 80")
        )
        script = [snapshot_frame(builders, rows=[builders.score_row()],
                                 round_index=1)]
        summary = run_recorder(spectator, script, profile)
        assert (spectator.calls[-1], summary.tables_seated) == (
            ("next_table",), 0
        )

    def test_the_expected_orientation_seats_the_table(self, profile, builders):
        spectator = FakeSpectator(
            scoreboard=ScoreboardReading(rows=((80, 0),), text="80 0")
        )
        script = [snapshot_frame(builders, rows=[builders.score_row()],
                                 round_index=1)]
        assert run_recorder(spectator, script, profile).tables_seated == 1

    def test_a_marked_belote_counts_toward_the_panel_column(self, profile,
                                                            builders):
        # The panel's column is the whole marked total, belote included. A
        # table where either side had marked one was refused as though its
        # sides were swapped, which is roughly a fifth of rounds (9A).
        spectator = FakeSpectator(
            scoreboard=ScoreboardReading(rows=((100, 0),), text="100 0")
        )
        script = [
            snapshot_frame(
                builders,
                rows=[builders.score_row(belote=(20, 0), marked_belote=(20, 0))],
                round_index=1,
            )
        ]
        assert run_recorder(spectator, script, profile).tables_seated == 1

    def test_an_unscored_game_skips_the_orientation_check(self, profile,
                                                          builders):
        # A game with no scored round has nothing to compare, and a panel
        # that reads zero rows is a correct answer rather than a failure.
        spectator = FakeSpectator(
            scoreboard=ScoreboardReading(rows=((0, 80),), text="0 80")
        )
        script = [snapshot_frame(builders, rows=(), round_index=None)]
        assert run_recorder(spectator, script, profile).tables_seated == 1


class TestSeating:
    def test_a_table_that_sends_no_snapshot_is_left(self, profile):
        # Seating without a snapshot means no seat map, so there is nothing
        # to record even if the table is perfect.
        clock = [0.0]
        spectator = FakeSpectator()
        script = [lambda: clock.__setitem__(0, 9999.0), frame("tick")]
        summary = run_recorder(spectator, script, profile,
                               monotonic=lambda: clock[0])
        assert (spectator.calls, summary.tables_seated) == (
            [("next_table",)], 0
        )

    def test_a_seat_that_times_out_is_left(self, profile):
        # The wait expiring, rather than the socket ending: a table that
        # simply never describes itself keeps its connection open.
        ticks = itertools.chain([0.0], itertools.repeat(29.99))
        spectator = FakeSpectator()
        summary = run_recorder(spectator, [], profile,
                               source=StallingFrameSource([]),
                               monotonic=lambda: next(ticks))
        assert (spectator.calls, summary.tables_seated) == (
            [("next_table",)], 0
        )

    def test_a_time_limit_reached_while_seating_stops_the_run(
        self, profile, builders
    ):
        clock = [0.0]
        script = [lambda: clock.__setitem__(0, 9999.0), frame("tick"),
                  snapshot_frame(builders)]
        summary = run_recorder(FakeSpectator(), script, profile,
                               limits=RecorderLimits(max_seconds=60.0),
                               monotonic=lambda: clock[0])
        assert (summary.tables_seated, summary.games_recorded) == (0, 0)

    def test_a_mirrored_snapshot_is_not_seated_twice(self, profile, builders):
        # Both connections carry every frame. The stream is reset at each
        # seat, so without a memory of its own the mirror of a snapshot just
        # rejected would be read as the next table's.
        spectator = FakeSpectator()
        rejected = snapshot_frame(builders, tournament=False)
        script = [rejected, frame(rejected.text, socket=1)]
        summary = run_recorder(spectator, script, profile)
        assert summary.tables_rejected == 1


class TestRecording:
    def test_a_watched_game_is_written_once(self, profile, session_frames,
                                            source_game):
        run_recorder(FakeSpectator(), session_frames(source_game), profile)
        assert len(records_in(profile)) == 1

    def test_the_written_record_holds_every_round(self, profile, session_frames,
                                                   source_game):
        summary = run_recorder(FakeSpectator(), session_frames(source_game),
                               profile)
        assert (len(records_in(profile)[0].rounds), summary.games_recorded) == (2, 1)

    def test_a_discovery_snapshot_never_reaches_the_record(
        self, profile, builders, session_frames, source_game
    ):
        # Discovery joins every candidate table and each visit emits one
        # snapshot. Keeping the first one seats four players from a table we
        # left — the record is complete, legal, and about the wrong people.
        # Script: reject-snapshot(table A) -> accept-snapshot(table B) -> play.
        rejected = snapshot_frame(builders, tournament=False, account="a",
                                  frame_id="sa")
        script = [rejected, *session_frames(source_game)]
        run_recorder(FakeSpectator(), script, profile)
        seats = records_in(profile)[0].seats
        assert all(seat.account.startswith("100") for seat in seats.values())

    def test_every_frame_reaches_the_raw_log(self, profile, tmp_root,
                                             session_frames, source_game):
        script = session_frames(source_game)
        with RawLogWriter(tmp_root / "session.jsonl") as log:
            run_recorder(FakeSpectator(), script, profile, raw=log)
        written = {line.text for line in read_raw_log(tmp_root / "session.jsonl")
                   if line.kind == "frame"}
        assert written == {item.text for item in script}

    def test_the_scoreboard_text_reaches_the_raw_log(self, profile, tmp_root,
                                                     builders):
        spectator = FakeSpectator(
            scoreboard=ScoreboardReading(rows=(), text="the panel, verbatim")
        )
        with RawLogWriter(tmp_root / "session.jsonl") as log:
            run_recorder(spectator, [snapshot_frame(builders)], profile, raw=log)
        panels = [line.text for line in read_raw_log(tmp_root / "session.jsonl")
                  if line.kind == "panel"]
        assert panels == ["the panel, verbatim"]


class TestBoundary:
    def test_a_new_deal_triggers_a_state_request(self, profile, session_frames,
                                                 source_game):
        spectator = FakeSpectator()
        run_recorder(spectator, session_frames(source_game), profile)
        assert ("request_state", "t1", "d2") in spectator.calls

    def test_the_first_deal_asks_for_nothing(self, profile, session_frames,
                                             source_game):
        # The snapshot that seated the table is the score read for the round
        # before it; asking again straight away costs a round trip and learns
        # nothing.
        spectator = FakeSpectator()
        run_recorder(spectator, session_frames(source_game), profile)
        assert ("request_state", "t1", "d1") not in spectator.calls

    def test_a_failed_resume_falls_back_to_the_panel(self, profile,
                                                     session_frames,
                                                     source_game):
        spectator = FakeSpectator(resume=False)
        run_recorder(spectator, session_frames(source_game), profile)
        assert spectator.calls.count(("read_scoreboard",)) == 2

    def test_the_score_reads_are_counted(self, profile, session_frames,
                                         source_game):
        health = HealthLog(write=lambda _: None)
        run_recorder(FakeSpectator(), session_frames(source_game), profile,
                     health=health)
        assert health.counters.score_reads_wire == 1

    def test_a_panel_that_answers_counts_as_a_read(self, profile,
                                                   session_frames, source_game):
        health = HealthLog(write=lambda _: None)
        spectator = FakeSpectator(
            resume=False,
            scoreboard=ScoreboardReading(rows=((80, 0),), text="80 0"),
        )
        run_recorder(spectator, session_frames(source_game), profile,
                     health=health)
        assert (health.counters.score_reads_panel,
                health.counters.score_reads_failed) == (1, 0)

    def test_a_panel_that_reads_nothing_is_a_failed_read(self, profile,
                                                          session_frames,
                                                          source_game):
        # Nothing downstream invents a score from it: the round simply has
        # no `round_scored`, and the schema says so.
        health = HealthLog(write=lambda _: None)
        run_recorder(FakeSpectator(resume=False), session_frames(source_game),
                     profile, health=health)
        assert health.counters.score_reads_failed == 1


class TestShiftTerms:
    def test_no_table_is_seated_once_the_seat_deadline_has_passed(self, profile, builders):
        spectator = FakeSpectator()
        summary = run_recorder(spectator, [snapshot_frame(builders)], profile,
                               limits=RecorderLimits(seat_until_s=0.0))
        assert (summary.tables_seated, summary.stop_reason, spectator.calls) == (
            0, StopReason.WINDOW_CLOSED, []
        )

    def test_a_wait_that_outlasts_the_seat_deadline_seats_nothing(self, profile, builders):
        # The window closes while the seat is still waiting for a snapshot:
        # the next frame, whatever it is, ends the wait rather than the table.
        clock = [0.0]
        spectator = FakeSpectator()
        script = [lambda: clock.__setitem__(0, 601.0), frame("tick"),
                  snapshot_frame(builders)]
        summary = run_recorder(spectator, script, profile,
                               limits=RecorderLimits(seat_until_s=600.0),
                               monotonic=lambda: clock[0])
        assert (summary.tables_seated, summary.stop_reason, spectator.calls) == (
            0, StopReason.WINDOW_CLOSED, []
        )

    def test_a_snapshot_arriving_after_the_seat_deadline_is_not_seated(self, profile, builders):
        clock = [0.0]
        spectator = FakeSpectator()
        script = [lambda: clock.__setitem__(0, 601.0), snapshot_frame(builders)]
        summary = run_recorder(spectator, script, profile,
                               limits=RecorderLimits(seat_until_s=600.0),
                               monotonic=lambda: clock[0])
        assert (summary.tables_seated, summary.stop_reason, spectator.calls) == (
            0, StopReason.WINDOW_CLOSED, []
        )

    def test_the_game_in_hand_carries_on_past_the_seat_deadline(
        self, profile, session_frames, game_builders
    ):
        # The seat deadline is the window's close; the game already being
        # watched is worth finishing, so only the next table is refused.
        clock = [0.0]
        spectator = FakeSpectator()
        ended = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        frames = session_frames(ended)
        # After the join snapshot and its mirror: the table is already seated.
        script = [*frames[:2], lambda: clock.__setitem__(0, 601.0), *frames[2:]]
        summary = run_recorder(spectator, script, profile,
                               limits=RecorderLimits(seat_until_s=600.0),
                               monotonic=lambda: clock[0])
        assert (summary.games_recorded, records_in(profile)[0].ended.reason,
                ("next_table",) in spectator.calls) == (1, EndReason.TARGET_REACHED, False)

    def test_a_finished_run_asks_for_no_further_table(
        self, profile, builders, session_frames, game_builders
    ):
        # A hop is traffic to the site; after the last game it buys nothing.
        spectator = FakeSpectator()
        ended = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        closing = snapshot_frame(builders, frame_id="sz", round_index=1,
                                 rows=[builders.score_row()])
        summary = run_recorder(spectator, [*session_frames(ended), closing], profile,
                               limits=RecorderLimits(max_games=1))
        assert (("next_table",) in spectator.calls, summary.stop_reason) == (
            False, StopReason.MAX_GAMES
        )

    def test_a_time_limit_is_reported_as_such(self, profile, session_frames, source_game):
        clock = [0.0]
        script = [
            *session_frames(source_game),
            lambda: clock.__setitem__(0, 9999.0),
            frame("tick"),
        ]
        summary = run_recorder(FakeSpectator(), script, profile,
                               limits=RecorderLimits(max_seconds=60.0),
                               monotonic=lambda: clock[0])
        assert summary.stop_reason is StopReason.TIME_LIMIT

    def test_a_run_whose_time_is_already_spent_reports_the_time_limit(self, profile, builders):
        summary = run_recorder(FakeSpectator(), [snapshot_frame(builders)], profile,
                               limits=RecorderLimits(max_seconds=0.0))
        assert (summary.tables_seated, summary.stop_reason) == (0, StopReason.TIME_LIMIT)

    def test_a_second_stop_keeps_the_first_reason(self, profile):
        # The cause is what a shift acts on: a refused egress that then also
        # ran the frames dry must still be reported as the egress.
        recorder = Recorder(FakeSpectator(), FakeFrameSource([]), profile,
                            HealthLog(write=lambda _: None))
        recorder._stop(StopReason.EGRESS_BLOCKED)
        recorder._stop(StopReason.SOURCE_ENDED)
        assert asyncio.run(recorder.run()).stop_reason is StopReason.EGRESS_BLOCKED


class TestEgressAtTheHop:
    def test_a_blocked_egress_stops_the_hop(self, profile, builders):
        lines: list[str] = []
        spectator = FakeSpectator()
        summary = run_recorder(spectator, [snapshot_frame(builders, tournament=False)], profile,
                               health=HealthLog(write=lines.append), egress=FakeEgress(BLOCKED))
        events = [json.loads(line)["event"] for line in lines]
        assert (spectator.calls, summary.stop_reason, "egress_blocked" in events) == (
            [], StopReason.EGRESS_BLOCKED, True
        )

    def test_an_open_egress_lets_the_hop_through(self, profile, builders):
        spectator = FakeSpectator()
        egress = FakeEgress(OPEN)
        run_recorder(spectator, [snapshot_frame(builders, tournament=False)], profile,
                     egress=egress)
        assert (spectator.calls, egress.calls) == ([("next_table",)], 1)

    def test_a_quiet_table_behind_a_blocked_egress_is_interrupted(
        self, profile, session_frames, source_game
    ):
        # Silence behind a dead tunnel is not a table breaking up: writing
        # `abandoned` would blame the players for our network.
        clock = [0.0]
        spectator = FakeSpectator()
        script = [
            *session_frames(source_game),
            lambda: clock.__setitem__(0, 9999.0),
            frame("tick"),
        ]
        summary = run_recorder(spectator, script, profile, monotonic=lambda: clock[0],
                               egress=FakeEgress(BLOCKED))
        assert (records_in(profile)[0].ended.reason, ("next_table",) in spectator.calls,
                summary.stop_reason) == (EndReason.INTERRUPTED, False,
                                         StopReason.EGRESS_BLOCKED)

    def test_a_quiet_table_behind_an_open_egress_is_abandoned_with_one_check(
        self, profile, session_frames, source_game
    ):
        # The check that cleared the table as stale also clears the hop:
        # a stale table costs one probe, not two.
        clock = [0.0]
        egress = FakeEgress(OPEN)
        script = [
            *session_frames(source_game),
            lambda: clock.__setitem__(0, 9999.0),
            frame("tick"),
        ]
        run_recorder(FakeSpectator(), script, profile, monotonic=lambda: clock[0],
                     egress=egress)
        assert (records_in(profile)[0].ended.reason, egress.calls) == (
            EndReason.ABANDONED, 1
        )


class TestEnding:
    def test_the_game_over_flag_ends_the_record_and_hops(
        self, profile, builders, session_frames, game_builders
    ):
        spectator = FakeSpectator()
        ended = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        # The table answers the closing request, so the session is still live
        # when it asks for the next table. A source that simply ran dry would
        # mean the browser is gone, and a hop is not asked of a dead session.
        closing = snapshot_frame(builders, frame_id="sz", round_index=1,
                                 rows=[builders.score_row()])
        run_recorder(spectator, [*session_frames(ended), closing], profile)
        record = records_in(profile)[0]
        assert (record.ended.reason, ("next_table",) in spectator.calls) == (
            EndReason.TARGET_REACHED,
            True,
        )

    def test_the_final_state_request_is_waited_for(
        self, profile, builders, session_frames, game_builders
    ):
        # The last round has had no deal after it, so its score has never
        # been asked for. Firing the request and writing at once would leave
        # the round the game was decided on as the one with no score.
        ended = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        closing = snapshot_frame(builders, frame_id="sz", round_index=1,
                                 rows=[builders.score_row()])
        # A straggler between the request and its answer: the drain keeps
        # buffering rather than stopping at the first thing it sees.
        straggler = frame(builders.play_frame(round_=2, trick=1, index=0,
                                              actor="p1", card="2w"))
        run_recorder(FakeSpectator(),
                     [*session_frames(ended), straggler, closing], profile)
        assert records_in(profile)[0].rounds[0].score is not None

    def test_a_refused_final_request_still_writes_the_record(
        self, profile, session_frames, game_builders
    ):
        ended = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        run_recorder(FakeSpectator(resume=False), session_frames(ended), profile)
        assert records_in(profile)[0].ended.reason is EndReason.TARGET_REACHED

    def test_the_wait_for_a_closing_snapshot_is_bounded(
        self, profile, session_frames, game_builders
    ):
        # A table that acknowledges the request and sends nothing must not
        # hold the session until the process is killed.
        clock = [0.0]
        ended = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        script = [*session_frames(ended),
                  lambda: clock.__setitem__(0, 9999.0), frame("tick")]
        summary = run_recorder(FakeSpectator(), script, profile,
                               monotonic=lambda: clock[0])
        assert summary.games_recorded == 1

    def test_a_silent_table_is_abandoned_by_the_watchdog(
        self, profile, session_frames, source_game
    ):
        clock = [0.0]
        script = [
            *session_frames(source_game),
            lambda: clock.__setitem__(0, 9999.0),
            frame("tick"),
        ]
        run_recorder(FakeSpectator(), script, profile,
                     monotonic=lambda: clock[0])
        assert records_in(profile)[0].ended.reason is EndReason.ABANDONED

    def test_an_observer_left_flag_alone_does_not_end_the_game(
        self, profile, builders, session_frames, source_game
    ):
        # "Left" says the spectator stopped watching, not that the table
        # finished. Closing the record on it would cut every game in half.
        spectator = FakeSpectator()
        script = _with_left_flag(builders, session_frames(source_game))
        run_recorder(spectator, script, profile)
        record = records_in(profile)[0]
        assert (len(record.rounds), ("next_table",) in spectator.calls) == (2, False)

    def test_a_table_that_goes_quiet_is_abandoned_when_the_wait_expires(
        self, profile, builders
    ):
        # The watchdog's real trigger: frames stop arriving on a socket that
        # is still open, so the wait expires rather than the source ending.
        ticks = itertools.chain([0.0, 0.0, 0.0], itertools.repeat(179.99))
        source = StallingFrameSource([snapshot_frame(builders)])
        summary = run_recorder(FakeSpectator(), [], profile, source=source,
                               monotonic=lambda: next(ticks))
        assert summary.tables_seated == 1

    def test_an_interrupt_before_a_snapshot_writes_nothing(self, profile,
                                                            builders):
        spectator = FakeSpectator()
        spectator.read_options = _raising_interrupt
        with pytest.raises(KeyboardInterrupt):
            run_recorder(spectator, [snapshot_frame(builders)], profile)
        assert not (profile.output.root / "games").exists()

    def test_an_interrupted_session_still_writes_what_it_saw(
        self, profile, session_frames, source_game
    ):
        spectator = FakeSpectator(on_resume=_interrupt)
        with pytest.raises(KeyboardInterrupt):
            run_recorder(spectator, session_frames(source_game), profile)
        assert records_in(profile)[0].ended.reason is EndReason.INTERRUPTED


class TestLimits:
    def test_max_games_stops_the_run(self, profile, session_frames,
                                     game_builders):
        first = game_builders.game_events(
            game_builders.round_events(1, *_ROUND_ONE),
            reason=EndReason.TARGET_REACHED,
        )
        script = [
            *session_frames(first),
            *session_frames(first, game="g2"),
        ]
        summary = run_recorder(FakeSpectator(), script, profile,
                               limits=RecorderLimits(max_games=1))
        assert (summary.games_recorded, len(summary.records)) == (1, 1)

    def test_a_time_limit_closes_the_game_it_was_watching(
        self, profile, session_frames, source_game
    ):
        clock = [0.0]
        script = [
            *session_frames(source_game),
            lambda: clock.__setitem__(0, 9999.0),
            frame("tick"),
        ]
        summary = run_recorder(FakeSpectator(), script, profile,
                               limits=RecorderLimits(max_seconds=60.0),
                               monotonic=lambda: clock[0])
        assert (summary.games_recorded,
                records_in(profile)[0].ended.reason) == (1, EndReason.OBSERVER_LEFT)

    def test_the_counters_track_the_wire_stream(self, profile, session_frames,
                                                source_game):
        # Every frame is mirrored on a second connection, so a run whose
        # deduped count is not half its received count lost a socket.
        health = HealthLog(write=lambda _: None)
        run_recorder(FakeSpectator(), session_frames(source_game), profile,
                     health=health)
        counters = health.counters
        assert (counters.frames_received == counters.frames_deduped * 2,
                counters.frames_skipped > 0,
                counters.games_recorded) == (True, True, 1)

    def test_a_heartbeat_is_written_while_watching(self, profile,
                                                   session_frames, source_game):
        lines: list[str] = []
        ticks = itertools.count(0, 1000)
        health = HealthLog(write=lines.append, monotonic=lambda: next(ticks))
        run_recorder(FakeSpectator(), session_frames(source_game), profile,
                     health=health)
        events = {json.loads(line)["event"] for line in lines}
        assert "heartbeat" in events


class TestRefusals:
    def test_a_session_the_parser_refuses_is_logged_and_left(
        self, profile, builders, monkeypatch
    ):
        # A table whose payload the parser cannot read must cost one hop, not
        # the whole shift.
        lines: list[str] = []
        health = HealthLog(write=lines.append)
        monkeypatch.setattr(
            "contrai_scraper.recorder.parse_session", _refusing_parser
        )
        spectator = FakeSpectator()
        summary = run_recorder(spectator, [snapshot_frame(builders)], profile,
                               health=health)
        events = {json.loads(line)["event"] for line in lines}
        assert (summary.games_recorded, "parse_failed" in events) == (0, True)

    def test_a_parser_note_reaches_the_health_log(
        self, profile, session_frames, source_game
    ):
        # The note says which round was dropped and why; a run that drops
        # every round while looking healthy is the failure this rules out.
        lines: list[str] = []
        health = HealthLog(write=lines.append)
        script = [item for item in session_frames(source_game)
                  if ",1,0,0" not in item.text]
        run_recorder(FakeSpectator(), script, profile, health=health)
        events = {json.loads(line)["event"] for line in lines}
        assert "parse_note" in events


#: The round the one-round games below are built from: dealer South,
#: declarer West — the seat after it, clockwise — eighty in spades, made.
_ROUND_ONE = (Position.SOUTH, Position.WEST, 80, Suit.SPADES, True,
              {TeamSide.NS: 0, TeamSide.EW: 170})


def _interrupt():
    """Stop the process the way a signal would, mid-game."""

    raise KeyboardInterrupt


async def _raising_interrupt(expected):
    """A browser call the process is killed during."""

    raise KeyboardInterrupt


def _refusing_parser(*args, **kwargs):
    """A parser that declines the session it was handed."""

    from contrai_scraper import ParseError

    raise ParseError("the session says nothing this parser understands")


def _with_left_flag(builders, frames):
    """The same session with an observer-left flag dropped in mid-game."""

    flagged = list(frames)
    marker = next(
        index for index, item in enumerate(flagged) if '"s1"' in item.text
    )
    flagged.insert(
        marker + 1,
        frame(builders.envelope("payload", "updateTable", {"gone": 1},
                                frame_id="mid")),
    )
    return flagged
