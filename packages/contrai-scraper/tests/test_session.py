"""Pins the whole pipeline: a record becomes frames and comes back identical."""

import dataclasses

import pytest
from contrai_core import (
    Card,
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    Rank,
    RedoubleBid,
    SlamLevel,
    Suit,
    TeamSide,
    TurnDirection,
)
from contrai_data import (
    CardPlayed,
    EndReason,
    GameEnded,
    GameStarted,
    Header,
    JoinPhase,
    ObservedFrom,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    SlamOutcome,
    project,
)

from contrai_scraper import (
    EventKey,
    ParseError,
    WireEvent,
    WireStream,
    order_events,
    parse_session,
    split_visits,
)
from contrai_scraper.parse.session import (
    _carried_over,
    _held,
    _last_trick,
    _round_scored,
    _slam,
    _swept_by,
    _totals_before,
)
from contrai_scraper.parse.snapshot import RowContract, ScoreRow


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


def _doubled_round(builders):
    """One round where West bids, North doubles and West redoubles.

    South deals, so the turn runs West, North, East, South. After the
    double, South — the doubler's partner — has nothing but a pass; after
    the redouble, nobody has anything else. Those four passes are the ones a
    table never sends: bids 4, 6, 7 and 8.
    """

    auction = (
        ContractBid(player=Position.WEST, value=80, suit=Suit.SPADES),
        DoubleBid(player=Position.NORTH),
        PassBid(player=Position.EAST),
        PassBid(player=Position.SOUTH),
        RedoubleBid(player=Position.WEST),
        PassBid(player=Position.NORTH),
        PassBid(player=Position.EAST),
        PassBid(player=Position.SOUTH),
    )
    return builders.round_events(
        1, Position.SOUTH, Position.WEST, 80, Suit.SPADES, True,
        {TeamSide.NS: 0, TeamSide.EW: 640}, auction=auction, multiplier=4)


#: The ``(round, seq)`` of every forced pass in ``_doubled_round``.
_FORCED = frozenset({(1, 4), (1, 6), (1, 7), (1, 8)})


class TestForcedPasses:
    def test_the_passes_the_wire_never_sent_come_back(
        self, profile, synthesize, game_builders
    ):
        game = game_builders.game_events(_doubled_round(game_builders))
        result = _parse(profile, synthesize(game, silent=_FORCED), game_id="obs-g1")
        assert _comparable(result.events) == _comparable(game)

    def test_the_restored_auction_closes(self, profile, synthesize, game_builders):
        # Without the passes put back the auction ends on a redouble, and the
        # projection calls the round unfinished.
        game = game_builders.game_events(_doubled_round(game_builders))
        parsed = _parse(profile, synthesize(game, silent=_FORCED)).events
        assert project(parsed).complete is True


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

    def test_a_join_whose_totals_cannot_be_placed_is_refused(self, profile, builders):
        # contrai-data refuses a join that does not name both sides' totals.
        # That has to reach the caller as a ParseError — which `parse` reports
        # per log and the recorder hops on — never as the data package's own
        # error escaping the command.
        payload = builders.snapshot_payload(round_index=4)
        del payload["state"]["round.g1"]["score"]["by_team"]["Y"]
        texts = [(builders.envelope("payload", "joinTable", payload), 0)]
        with pytest.raises(ParseError, match="totals"):
            _parse(profile, texts)


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
        self, profile, source_game, synthesize, builders, game_builders
    ):
        # The declarer's bid arrives as a pass, so every seat passes.
        declarer = game_builders.handle_of_seat[Position.WEST]
        passed = builders.bid_frame(round_=1, seq=1, actor=declarer, payload=None)
        texts = [
            (passed if '"id": "b1-1"' in text else text, socket)
            for text, socket in synthesize(source_game)
        ]
        result = _parse(profile, texts)
        assert result.skipped_rounds == (1,)
        assert any("contract" in note for note in result.notes)

    def test_a_round_missing_a_bid_is_skipped(self, profile, source_game, synthesize):
        # North's pass never arrives, but North could have doubled: that is a
        # lost bid, not a forced pass, and the round is refused rather than
        # repaired.
        texts = [(text, socket) for text, socket in synthesize(source_game)
                 if '"id": "b1-2"' not in text]
        result = _parse(profile, texts)
        assert result.skipped_rounds == (1,)
        assert any("missing" in note for note in result.notes)


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


def _sweep_plays(*, winner_of_last=Position.WEST):
    """Eight whole tricks, all but the last taken by West trumping in.

    Not a legal deal and not meant to be: :func:`_swept_by` groups plays and
    asks core who won each trick, so the only thing that has to be true here
    is the shape. Three seats discard off-suit and West cuts with a spade,
    which is a win under core's own rule rather than under this test's.
    """

    hearts, diamonds, clubs, spades = (
        [Card(suit, rank) for rank in Rank]
        for suit in (Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES)
    )
    plays = []
    for index in range(8):
        last = index == 7
        if last and winner_of_last is not Position.WEST:
            # Nobody trumps, so the seat that led the only heart takes it.
            trick = [(Position.NORTH, hearts[index]), (Position.EAST, diamonds[index]),
                     (Position.SOUTH, clubs[index]), (Position.WEST, diamonds[0])]
        else:
            trick = [(Position.NORTH, hearts[index]), (Position.EAST, diamonds[index]),
                     (Position.SOUTH, clubs[index]), (Position.WEST, spades[index])]
        plays += [
            CardPlayed(round=1, trick=index + 1, position=seat, card=card,
                       derived=False, think_ms=None, ts="2026-09-16T00:00:00Z")
            for seat, card in trick
        ]
    return plays


class TestUnannouncedSlam:
    def test_a_side_taking_every_trick_is_the_sweeping_side(self):
        assert _swept_by(_sweep_plays(), Suit.SPADES) is TeamSide.EW

    def test_a_shared_round_sweeps_for_nobody(self):
        assert _swept_by(_sweep_plays(winner_of_last=Position.NORTH),
                         Suit.SPADES) is None

    def test_a_round_short_of_eight_tricks_sweeps_for_nobody(self):
        # A game joined mid-round, or one the watchdog cut off.
        plays = [play for play in _sweep_plays() if play.trick < 8]
        assert _swept_by(plays, Suit.SPADES) is None

    def test_a_trick_short_of_four_plays_sweeps_for_nobody(self):
        plays = _sweep_plays()
        assert _swept_by(plays[:-1], Suit.SPADES) is None

    def test_a_declaring_side_that_swept_is_an_unannounced_slam(self):
        # The engine writes UNANNOUNCED for exactly this round, and a record
        # saying "none" is contradicted by its own plays — which is what the
        # verifier caught live on 2026-09-16 (obs-3bb24810 round 4). There
        # is no multiplier argument: a doubled sweep is still eight tricks
        # taken uncalled, and the §9.6 knobs price it, not the parser.
        contract = ContractBid(player=Position.WEST, value=130, suit=Suit.SPADES)
        assert _slam(contract, _sweep_plays()) is SlamOutcome.UNANNOUNCED

    def test_a_sweep_by_the_defence_is_not_the_declarer_s_slam(self):
        contract = ContractBid(player=Position.NORTH, value=130, suit=Suit.SPADES)
        assert _slam(contract, _sweep_plays()) is SlamOutcome.NONE

    def test_an_ordinary_round_stays_none(self):
        contract = ContractBid(player=Position.WEST, value=130, suit=Suit.SPADES)
        assert _slam(contract,
                     _sweep_plays(winner_of_last=Position.NORTH)) is SlamOutcome.NONE

    def test_a_bid_slam_outranks_a_swept_one(self):
        # The declarer announced it, so that is what the round was.
        contract = ContractBid(player=Position.WEST, value=SlamLevel.SLAM,
                               suit=Suit.SPADES)
        assert _slam(contract, _sweep_plays()) is SlamOutcome.SLAM


class TestLastTrick:
    # With North taking the eighth, West's seven tricks hold 108 and North's
    # 33 — _sweep_plays reuses a card, so the deck is short of the 152.
    _SHARED = dict(winner_of_last=Position.NORTH)

    def test_card_points_with_the_bonus_on_the_taker_name_it(self):
        taken = {TeamSide.NS: 43, TeamSide.EW: 108}
        assert _last_trick(_sweep_plays(**self._SHARED), Suit.SPADES,
                           taken) is TeamSide.NS

    def test_card_points_with_the_bonus_elsewhere_name_nobody(self):
        # Core's rule and the site's split disagree, so neither is written
        # down: the card-points check is left to say which is wrong.
        taken = {TeamSide.NS: 33, TeamSide.EW: 118}
        assert _last_trick(_sweep_plays(**self._SHARED), Suit.SPADES,
                           taken) is None

    def test_a_sweep_names_the_sweeper_whatever_its_substitute(self):
        # The site states the flat 250 in place of the pile.
        taken = {TeamSide.NS: 0, TeamSide.EW: 250}
        assert _last_trick(_sweep_plays(), Suit.SPADES, taken) is TeamSide.EW

    def test_a_round_short_of_eight_whole_tricks_names_nobody(self):
        taken = {TeamSide.NS: 43, TeamSide.EW: 108}
        assert _last_trick(_sweep_plays(**self._SHARED)[:-1], Suit.SPADES,
                           taken) is None


def _snap(table, at=None):
    """A join snapshot naming one table, with nothing else the split reads."""

    return WireEvent(kind="joinTable", key=None, data={"table": {"id": table}},
                     received_ms=at)


def _played(game, round_, at=None):
    """One in-game event, keyed to its game the way the wire keys it."""

    return WireEvent(
        kind=f"{game},{round_},0,0,tos,p1",
        key=EventKey(game=game, round=round_, trick=0, position=0,
                     verb="tos", player="p1"),
        data={}, received_ms=at,
    )


def _lifecycle(at=None):
    """A table update, which carries no key and no game of its own."""

    return WireEvent(kind="updateTable", key=None, data={}, received_ms=at)


class TestSplitVisits:
    def test_two_tables_become_two_visits(self, profile):
        events = [_snap("t1"), _played("g1", 1), _snap("t2"), _played("g2", 1)]
        visits = split_visits(events, profile)
        assert [len(visit) for visit in visits] == [2, 2]

    def test_the_mirrored_copy_of_a_snapshot_stays_in_its_visit(self, profile):
        # Both sockets carry every frame, so a table describes itself twice.
        events = [_snap("t1"), _snap("t1"), _played("g1", 1)]
        assert len(split_visits(events, profile)) == 1

    def test_a_revisited_table_is_a_new_visit(self, profile):
        # The same table later in the sweep is a different sitting, and its
        # game may well be a different game.
        events = [_snap("t1"), _played("g1", 1), _snap("t2"), _played("g2", 1),
                  _snap("t1"), _played("g3", 1)]
        assert len(split_visits(events, profile)) == 3

    def test_frames_before_the_first_snapshot_are_dropped(self, profile):
        # Nothing names the table they came from, so there is no visit to
        # file them under.
        events = [_played("g0", 1), _snap("t1"), _played("g1", 1)]
        visits = split_visits(events, profile)
        # The snapshot stays: it is what names the seats.
        assert [len(visit) for visit in visits] == [2]
        assert "g0" not in {event.key.game for event in visits[0] if event.key}

    def test_an_events_own_game_decides_its_visit_not_its_arrival(self, profile):
        # A table's last events can still be in flight when the hop lands.
        # Filing them by arrival puts one game's rounds inside another's
        # visit, where their round numbers collide with its own.
        events = [_snap("t1"), _played("g1", 1),
                  _snap("t2"), _played("g1", 2), _played("g2", 1)]
        first, second = split_visits(events, profile)
        assert [e.key.game for e in first if e.key] == ["g1", "g1"]
        assert [e.key.game for e in second if e.key] == ["g2"]

    def test_a_lifecycle_event_stays_with_the_visit_it_arrived_in(self, profile):
        # It carries no game of its own, and the game it speaks about is the
        # one being watched when it arrived.
        events = [_snap("t1"), _played("g1", 1), _lifecycle(),
                  _snap("t2"), _played("g2", 1)]
        first, second = split_visits(events, profile)
        assert [event.kind for event in first] == [
            "joinTable", "g1,1,0,0,tos,p1", "updateTable"
        ]
        assert [event.kind for event in second] == ["joinTable", "g2,1,0,0,tos,p1"]

    def test_a_log_with_no_snapshot_yields_no_visit(self, profile):
        assert split_visits([_played("g1", 1)], profile) == ()


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


def _with_draw(builders, texts, *extra):
    """A session caught from its first card: the draw right after the join.

    Two of the four draws, as the chase probe saw: it always arrived with the
    draw already under way.
    """

    draw = [
        (builders.envelope("payload", f"g1,0,0,{index},lots,p{index + 1}", card,
                           frame_id=f"draw{index}"), 0)
        for index, card in enumerate(("2w", "5x"))
    ]
    extra_texts = [(text, 0) for text in extra]
    return [*texts[:2], *draw, *extra_texts, *texts[2:]]


class TestTheDraw:
    def test_a_caught_openings_draw_leaves_the_record_as_it_was(
        self, profile, builders, source_game, synthesize
    ):
        result = _parse(profile, _with_draw(builders, synthesize(source_game)),
                        game_id="obs-g1")
        assert (_comparable(result.events), result.notes, result.skipped_rounds) == (
            _comparable(source_game), (), ())

    def test_another_verb_at_round_0_is_left_out_and_said(
        self, profile, builders, source_game, synthesize
    ):
        stray = builders.envelope("payload", "g1,0,0,0,card,p1", "2w", frame_id="odd")
        result = _parse(profile, _with_draw(builders, synthesize(source_game), stray),
                        game_id="obs-g1")
        assert (_comparable(result.events) == _comparable(source_game),
                result.notes) == (True, (
                    "round 0: 1 event(s) under a verb other than the draw's were "
                    "left out with it",))

    def test_the_draws_verb_outside_round_0_is_left_out_and_said(
        self, profile, builders, source_game, synthesize
    ):
        stray = builders.envelope("payload", "g1,2,0,0,lots,p1", "2w", frame_id="odd")
        result = _parse(profile, _with_draw(builders, synthesize(source_game), stray),
                        game_id="obs-g1")
        assert (_comparable(result.events) == _comparable(source_game),
                result.notes) == (True, (
                    "1 event(s) under the draw's verb were addressed outside "
                    "round 0, and left out",))

    def test_with_no_draw_verb_named_round_0_is_left_out_all_the_same(
        self, profile, builders, source_game, synthesize
    ):
        unnamed = dataclasses.replace(
            profile, wire=dataclasses.replace(profile.wire, draw_verb=None))
        result = _parse(unnamed, _with_draw(builders, synthesize(source_game)),
                        game_id="obs-g1")
        assert (_comparable(result.events) == _comparable(source_game),
                result.skipped_rounds, "names no draw verb" in result.notes[0]) == (
            True, (), True)


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


def _row(*, made=True, marked=None, marked_belote=None):
    """A score row for W's 80 in diamonds, 81 / 81 unless told otherwise."""

    return ScoreRow(
        made=made,
        contract=RowContract(value=80, suit=Suit.DIAMONDS, multiplier=1),
        taken={TeamSide.NS: 81, TeamSide.EW: 81},
        belote={side: 0 for side in TeamSide},
        marked=marked or {TeamSide.NS: (81, 0), TeamSide.EW: (0, 0)},
        marked_belote=marked_belote or {side: 0 for side in TeamSide},
    )


_W80 = ContractBid(player=Position.WEST, value=80, suit=Suit.DIAMONDS)


class TestHeldRows:
    def test_made_with_the_declarer_marked_nothing_is_held(self):
        # obs-f3c28d3b round 3, as the site states it.
        assert _held(_row(), _W80) is True

    def test_an_ordinary_made_row_is_not(self):
        row = _row(marked={TeamSide.NS: (14, 0), TeamSide.EW: (148, 80)})
        assert _held(row, _W80) is False

    def test_a_failed_row_is_not(self):
        row = _row(made=False,
                   marked={TeamSide.NS: (160, 160), TeamSide.EW: (0, 0)})
        assert _held(row, _W80) is False


class TestCarriedOver:
    _R4 = dict(marked={TeamSide.NS: (14, 0), TeamSide.EW: (148, 90)})

    def test_the_observed_payout(self):
        # obs-f3c28d3b round 4: 129 / 496 before, 143 / 895 after.
        carry = _carried_over(
            _row(**self._R4),
            {TeamSide.NS: 143, TeamSide.EW: 895},
            {TeamSide.NS: 129, TeamSide.EW: 496},
        )
        assert carry == {TeamSide.NS: 0, TeamSide.EW: 161}

    def test_an_ordinary_round_carries_nothing(self):
        carry = _carried_over(
            _row(**self._R4),
            {TeamSide.NS: 143, TeamSide.EW: 734},
            {TeamSide.NS: 129, TeamSide.EW: 496},
        )
        assert carry == {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_credited_belote_is_not_a_carry(self):
        carry = _carried_over(
            _row(marked={TeamSide.NS: (14, 0), TeamSide.EW: (148, 90)},
                 marked_belote={TeamSide.NS: 0, TeamSide.EW: 20}),
            {TeamSide.NS: 143, TeamSide.EW: 754},
            {TeamSide.NS: 129, TeamSide.EW: 496},
        )
        assert carry == {TeamSide.NS: 0, TeamSide.EW: 0}

    @pytest.mark.parametrize("known", ["totals", "before"])
    def test_unknown_totals_mean_an_unknown_carry(self, known):
        totals = {TeamSide.NS: 143, TeamSide.EW: 895}
        before = {TeamSide.NS: 129, TeamSide.EW: 496}
        carry = _carried_over(
            _row(**self._R4),
            totals if known == "totals" else None,
            before if known == "before" else None,
        )
        assert carry is None

    def test_a_negative_residual_is_not_a_payout(self):
        carry = _carried_over(
            _row(**self._R4),
            {TeamSide.NS: 100, TeamSide.EW: 734},
            {TeamSide.NS: 129, TeamSide.EW: 496},
        )
        assert carry is None


class TestTotalsBefore:
    _AFTER_2 = {TeamSide.NS: 48, TeamSide.EW: 496}

    def test_the_join_round_reads_the_join_snapshot(self):
        joined = ObservedFrom(round=3, phase=JoinPhase.PLAY,
                              totals=self._AFTER_2)
        assert _totals_before(3, {}, joined) == self._AFTER_2

    def test_a_later_round_reads_the_round_before(self):
        scores = {2: (_row(), self._AFTER_2)}
        assert _totals_before(3, scores, None) == self._AFTER_2

    def test_a_round_before_without_totals_is_unknown(self):
        assert _totals_before(3, {2: (_row(), None)}, None) is None

    def test_a_missing_round_before_is_unknown(self):
        assert _totals_before(3, {}, None) is None


class TestHeldRoundScored:
    def test_a_held_row_is_recorded_as_held(self):
        scored = _round_scored(
            3, _W80, (), (_row(), {TeamSide.NS: 129, TeamSide.EW: 496}), (),
            "2026-09-24T00:00:00Z", {TeamSide.NS: 48, TeamSide.EW: 496},
        )
        assert scored.outcome is RoundOutcome.HELD
        assert scored.carried_over == {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_a_round_with_no_totals_before_has_an_unknown_carry(self):
        scored = _round_scored(
            3, _W80, (), (_row(), {TeamSide.NS: 129, TeamSide.EW: 496}), (),
            "2026-09-24T00:00:00Z", None,
        )
        assert scored.carried_over is None
