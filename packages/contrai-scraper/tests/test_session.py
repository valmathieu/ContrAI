"""Pins the whole pipeline: a record becomes frames and comes back identical."""

import dataclasses

import pytest
from contrai_core import Position, SlamLevel, Suit, TeamSide, TurnDirection
from contrai_data import (
    CardPlayed,
    EndReason,
    GameEnded,
    GameStarted,
    Header,
    RoundDealt,
    RoundScored,
    project,
)

from contrai_scraper import ParseError, WireStream, order_events, parse_session


def _comparable(events):
    """Everything a record says that the wire could have carried.

    Dropped: the header's identity and generator, which the scraper mints
    rather than observes, and every timestamp, which is when a frame was
    *seen* rather than what it said. Anything else that differs is a parser
    disagreement with the record it came from.
    """

    trimmed = []
    for event in events:
        if isinstance(event, Header):
            trimmed.append(("header", event.format, event.source))
            continue
        fields = {
            name: value
            for name, value in dataclasses.asdict(event).items()
            if name != "ts"
        }
        trimmed.append((type(event).__name__, fields))
    return trimmed


def _without_score_reads(texts):
    """The session with every snapshot but the opening one removed.

    The opening snapshot is what names the seats, so it has to stay; what goes
    is every later read, which is where the score rows come from.
    """

    return [
        (text, socket) for text, socket in texts
        if "joinTable" not in text or '"id": "s0"' in text
    ]


def _parse(profile, texts, **kwargs):
    """Push frame texts through the real stream and parse the session."""

    stream = WireStream(profile.wire)
    events = [
        event
        for text, socket in texts
        if (event := stream.ingest(text, socket=socket)) is not None
    ]
    return parse_session(order_events(events), profile, **kwargs)


class TestRoundTrip:
    def test_synthesized_frames_reproduce_the_record(
        self, profile, source_game, synthesize
    ):
        result = _parse(profile, synthesize(source_game), game_id="obs-g1")
        assert _comparable(result.events) == _comparable(source_game)

    def test_the_mirrored_socket_changes_nothing(
        self, profile, source_game, synthesize
    ):
        both = _parse(profile, synthesize(source_game))
        one_only = _parse(
            profile, [(text, 0) for text, socket in synthesize(source_game)
                      if socket == 0]
        )
        assert _comparable(both.events) == _comparable(one_only.events)

    def test_the_record_projects_complete(self, profile, source_game, synthesize):
        parsed = _parse(profile, synthesize(source_game)).events
        assert project(parsed).complete is True

    def test_the_eighth_trick_comes_back_marked_derived(
        self, profile, source_game, synthesize
    ):
        # Twenty-eight plays reach the wire. The four that do not are flagged,
        # so a reader can tell what was observed from what was inferred.
        parsed = _parse(profile, synthesize(source_game)).events
        plays = [event for event in parsed if isinstance(event, CardPlayed)]
        derived = {event.trick for event in plays if event.derived}
        assert derived == {8}
        assert len([event for event in plays if event.derived]) == 8

    def test_the_seats_carry_their_accounts_and_levels(
        self, profile, source_game, synthesize
    ):
        started = next(
            event for event in _parse(profile, synthesize(source_game)).events
            if isinstance(event, GameStarted)
        )
        assert started.seats[Position.NORTH].account == "1001"
        assert started.seats[Position.EAST].level is None

    def test_the_game_id_defaults_to_the_wires_own(
        self, profile, source_game, synthesize
    ):
        header = _parse(profile, synthesize(source_game)).events[0]
        assert header.game_id == "obs-g1"

    def test_the_record_names_the_clockwise_ruleset(
        self, profile, source_game, synthesize
    ):
        started = next(
            event for event in _parse(profile, synthesize(source_game)).events
            if isinstance(event, GameStarted)
        )
        assert started.ruleset.config.turn_direction is TurnDirection.CLOCKWISE


class TestSkippedRounds:
    def test_a_round_joined_mid_play_is_skipped_entirely(
        self, profile, source_game, synthesize, game_builders
    ):
        # The snapshot describes the last completed round, so the joining
        # round's hands are unrecoverable and fabricating them is worse than
        # losing them.
        texts = [
            (text, socket)
            for text, socket in synthesize(source_game)
            if ",1,0,0" not in text          # the round-1 deal never arrives
        ]
        result = _parse(profile, texts)
        dealt = {event.round for event in result.events
                 if isinstance(event, RoundDealt)}
        assert dealt == {2}
        assert result.skipped_rounds == (1,)

    def test_a_skipped_round_is_reported(self, profile, source_game, synthesize):
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if ",1,0,0" not in text]
        assert any("1" in note for note in _parse(profile, texts).notes)

    def test_a_round_cut_short_is_skipped(
        self, profile, source_game, synthesize
    ):
        # Fewer than twenty-eight plays and the eighth trick cannot be built.
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if ",2,7," not in text]
        dealt = {event.round for event in _parse(profile, texts).events
                 if isinstance(event, RoundDealt)}
        assert dealt == {1}


class TestScoring:
    def test_a_round_with_no_score_row_emits_no_round_scored(
        self, profile, source_game, synthesize
    ):
        # D3: a round the wire never scored gets no round_scored at all. A
        # fabricated total is exactly what the verifier cannot catch.
        result = _parse(profile, _without_score_reads(synthesize(source_game)))
        assert [e for e in result.events if isinstance(e, RoundScored)] == []

    def test_a_game_that_ends_without_a_score_read_has_null_totals(
        self, profile, source_game, synthesize
    ):
        ended = _parse(
            profile, _without_score_reads(synthesize(source_game))
        ).events[-1]
        assert isinstance(ended, GameEnded) and ended.totals is None

    def test_the_score_source_is_the_snapshot(
        self, profile, source_game, synthesize
    ):
        scored = [event for event in _parse(profile, synthesize(source_game)).events
                  if isinstance(event, RoundScored)]
        assert {event.source.value for event in scored} == {"snapshot"}

    def test_the_declarer_comes_from_the_auction(
        self, profile, source_game, synthesize
    ):
        scored = [event for event in _parse(profile, synthesize(source_game)).events
                  if isinstance(event, RoundScored)]
        assert [event.declarer for event in scored] == [
            Position.WEST, Position.SOUTH]


class TestEnding:
    def test_a_table_that_closes_ends_the_record(
        self, profile, source_game, synthesize
    ):
        ended = _parse(profile, synthesize(source_game)).events[-1]
        assert ended.reason is EndReason.OBSERVER_LEFT

    def test_a_table_update_that_says_neither_leaves_it_interrupted(
        self, profile, source_game, synthesize, builders
    ):
        # The table talks about more than endings; an update that mentions
        # neither must not be read as one.
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if "updateTable" not in text]
        texts.append(
            (builders.envelope("payload", "updateTable", {"watchers": 3}), 0))
        assert _parse(profile, texts).events[-1].reason is EndReason.INTERRUPTED

    def test_a_session_that_just_stops_is_interrupted(
        self, profile, source_game, synthesize
    ):
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if "updateTable" not in text]
        assert _parse(profile, texts).events[-1].reason is EndReason.INTERRUPTED

    def test_a_caller_can_state_how_the_game_ended(self, profile, synthesize,
                                                   source_game):
        # A watchdog giving up on a silent table is invisible to the wire, so
        # nothing in the stream can say `abandoned` — only the recorder knows.
        result = _parse(profile, synthesize(source_game),
                        end_reason=EndReason.ABANDONED)
        assert result.events[-1].reason is EndReason.ABANDONED

    def test_a_stated_reason_beats_the_wires_own_flag(self, profile, synthesize,
                                                      source_game):
        result = _parse(profile, synthesize(source_game),
                        end_reason=EndReason.INTERRUPTED)
        assert result.events[-1].reason is EndReason.INTERRUPTED


class TestRefusals:
    def test_a_session_with_no_snapshot_is_refused(self, profile, builders):
        # Without the snapshot there is no seat map, and a record whose seats
        # were guessed is worse than no record.
        texts = [(builders.play_frame(actor="p1", card="2w"), 0)]
        with pytest.raises(ParseError, match="snapshot"):
            _parse(profile, texts)

    def test_a_session_with_no_rounds_still_produces_a_record(
        self, profile, builders
    ):
        texts = [(builders.envelope("payload", "joinTable",
                                    builders.snapshot_payload()), 0)]
        events = _parse(profile, texts, game_id="obs-empty").events
        assert [type(event).__name__ for event in events] == [
            "Header", "GameStarted", "GameEnded"]


class TestObservedFrom:
    def test_a_mid_game_join_records_where_it_landed(self, profile, builders):
        # A snapshot that already knows a completed round means the session
        # began part-way through the game.
        texts = [(builders.envelope("payload", "joinTable",
                                    builders.snapshot_payload(round_index=4)), 0)]
        started = _parse(profile, texts).events[1]
        assert started.observed_from is not None
        assert started.observed_from.round == 5
        assert started.observed_from.totals == {TeamSide.NS: 40, TeamSide.EW: 60}


class TestUnresolvableRounds:
    def test_a_round_with_no_plays_leaves_the_dealer_unknown(
        self, profile, source_game, synthesize
    ):
        # Four candidate deals and nothing to choose between them. The dealer
        # decides where the auction started, so a guess would move the whole
        # round one seat round the table.
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if '"id": "p1-' not in text]
        result = _parse(profile, texts)
        assert result.skipped_rounds == (1,)
        assert any("dealer" in note for note in result.notes)

    def test_a_round_whose_auction_reached_no_contract_is_skipped(
        self, profile, source_game, synthesize
    ):
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if '"id": "b1-1"' not in text]
        result = _parse(profile, texts)
        assert result.skipped_rounds == (1,)
        assert any("contract" in note for note in result.notes)


class TestSlams:
    def test_a_slam_contract_is_recorded_as_one(
        self, profile, synthesize, game_builders
    ):
        game = game_builders.game_events(
            game_builders.round_events(
                1, Position.SOUTH, Position.WEST, SlamLevel.SLAM, Suit.SPADES,
                True, {TeamSide.NS: 0, TeamSide.EW: 250}),
        )
        scored = next(event for event in _parse(profile, synthesize(game)).events
                      if isinstance(event, RoundScored))
        assert scored.slam.value == "slam"
        assert scored.contract.value is SlamLevel.SLAM

    def test_a_solo_slam_contract_is_recorded_as_one(
        self, profile, synthesize, game_builders
    ):
        game = game_builders.game_events(
            game_builders.round_events(
                1, Position.SOUTH, Position.WEST, SlamLevel.SOLO_SLAM,
                Suit.SPADES, True, {TeamSide.NS: 0, TeamSide.EW: 500}),
        )
        scored = next(event for event in _parse(profile, synthesize(game)).events
                      if isinstance(event, RoundScored))
        assert scored.slam.value == "solo_slam"


class TestScoreRowWalk:
    def test_rows_for_rounds_before_the_session_are_dropped(
        self, profile, builders
    ):
        # A snapshot carries every row of the game so far, including rounds
        # played before anyone was watching. Walking backwards past round one
        # would attribute them to rounds that do not exist.
        payload = builders.snapshot_payload(
            round_index=1, rows=(builders.score_row(),) * 3)
        texts = [(builders.envelope("payload", "joinTable", payload), 0)]
        assert _parse(profile, texts).events[-1].totals is not None


class TestTargetReached:
    def test_a_table_that_finishes_its_game_says_so(
        self, profile, synthesize, game_builders
    ):
        game = game_builders.game_events(
            game_builders.round_events(
                1, Position.SOUTH, Position.WEST, 80, Suit.SPADES, True,
                {TeamSide.NS: 0, TeamSide.EW: 2000}),
            reason=EndReason.TARGET_REACHED,
        )
        ended = _parse(profile, synthesize(game)).events[-1]
        assert ended.reason is EndReason.TARGET_REACHED
