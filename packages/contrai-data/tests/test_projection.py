"""Pins the fold: a flat event stream back into rounds, and what it re-derives."""

import dataclasses

import pytest
from contrai_core import (
    Auction,
    Card,
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    Rank,
    RedoubleBid,
    RuleConfig,
    Suit,
    TeamSide,
)

from contrai_data import (
    BeloteHeld,
    BidMade,
    CardPlayed,
    GameEnded,
    HandsDerivation,
    RecordFormatError,
    RecordWriter,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    load_game,
    project,
)
from contrai_data.projection import _RoundBuilder

TS = "2026-09-10T18:18:15Z"

#: Seats in canonical order, so "later in the trick than West" can be
#: expressed as an index. ``Position`` is a plain ``Enum`` and deliberately
#: does not order — comparing two members raises ``TypeError``.
SEATS = list(Position)


class TestShape:
    def test_three_rounds_in_file_order(self, three_round_game):
        game = project(three_round_game)
        assert [round_.number for round_ in game.rounds] == [1, 2, 3]

    def test_carries_the_header_and_the_table(self, three_round_game):
        game = project(three_round_game)
        assert game.preset == "classic"
        assert game.ruleset == RuleConfig()
        assert game.header.game_id == "engine-20260910T181815Z-a1b2c3"
        assert set(game.seats) == set(Position)

    def test_the_game_is_complete(self, three_round_game):
        game = project(three_round_game)
        assert [round_.complete for round_ in game.rounds] == [True, True, True]
        assert game.complete is True

    def test_a_game_without_an_end_is_incomplete(self, three_round_game):
        game = project([e for e in three_round_game if not isinstance(e, GameEnded)])
        assert game.ended is None
        assert game.complete is False

    def test_a_truncated_file_is_never_complete(self, three_round_game):
        assert project(three_round_game, truncated=True).complete is False

    def test_a_game_with_no_rounds_at_all(self, three_round_game):
        # A record whose observer left during the very first auction: a
        # header, a table, and nothing else. It must fold, not crash.
        events = [three_round_game[0], three_round_game[1], three_round_game[-1]]
        game = project(events)
        assert game.rounds == ()
        assert game.complete is True

    def test_dealer_hands_and_derivation_survive_the_fold(self, three_round_game):
        round_ = project(three_round_game).rounds[0]
        assert round_.dealer is Position.EAST
        assert round_.hands_derivation is HandsDerivation.SELF_PLAY
        assert round_.hands[Position.NORTH][0] == Card(Suit.SPADES, Rank.SEVEN)

    def test_where_we_joined_is_carried(self, three_round_game):
        assert project(three_round_game).observed_from is None


class TestDerivations:
    def test_the_contract_comes_from_the_auction(self, three_round_game):
        contract = project(three_round_game).rounds[0].contract
        assert contract.declarer is Position.NORTH
        assert (contract.value, contract.suit) == (80, Suit.SPADES)
        assert contract.get_multiplier() == 1

    def test_an_all_pass_round_has_no_contract(self, three_round_game):
        assert project(three_round_game).rounds[1].contract is None

    def test_the_trump_suit_follows_the_contract(self, three_round_game):
        game = project(three_round_game)
        assert game.rounds[0].trump_suit is Suit.SPADES
        assert game.rounds[1].trump_suit is None
        assert game.rounds[2].trump_suit is Suit.HEARTS

    def test_eight_completed_tricks_with_no_partial_remainder(self, three_round_game):
        round_ = project(three_round_game).rounds[0]
        assert len(round_.tricks) == 8
        assert all(len(trick) == 4 for trick in round_.tricks)
        assert round_.current_trick == ()

    def test_trick_winners_are_derived_through_the_trump_rules(self, three_round_game):
        # Round 1 is 80 spades and North holds every spade, so North trumps
        # all eight tricks. Round 3 is 80 hearts into West's hand, so West
        # takes all eight off the declarer.
        game = project(three_round_game)
        assert game.rounds[0].trick_winners == (Position.NORTH,) * 8
        assert game.rounds[2].trick_winners == (Position.WEST,) * 8

    def test_an_all_pass_round_has_no_winners(self, three_round_game):
        assert project(three_round_game).rounds[1].trick_winners == ()

    def test_the_eighth_trick_is_flagged_as_derived(self, three_round_game):
        round_ = project(three_round_game).rounds[0]
        assert round_.derived_tricks == (False,) * 7 + (True,)

    def test_belotes_attach_to_their_round(self, three_round_game):
        assert project(three_round_game).rounds[0].belotes == ()

    def test_a_belote_lands_on_the_round_it_names(self, three_round_game):
        held = BeloteHeld(
            round=3,
            position=Position.WEST,
            cards=(Card(Suit.HEARTS, Rank.KING), Card(Suit.HEARTS, Rank.QUEEN)),
            announced=True,
            ts=TS,
        )
        events = list(three_round_game)
        events.insert(-1, held)
        game = project(events)
        assert game.rounds[2].belotes == (held,)
        assert game.rounds[0].belotes == ()

    def test_the_outcome_comes_from_the_score_when_there_is_one(self, three_round_game):
        outcomes = [round_.outcome for round_ in project(three_round_game).rounds]
        assert outcomes == [
            RoundOutcome.MADE,
            RoundOutcome.ALL_PASS,
            RoundOutcome.FAILED,
        ]

    def test_all_pass_is_derivable_without_a_score(self, three_round_game):
        # D3: a round with no score source emits no ``round_scored`` at all,
        # so "was this round passed out" has to come off the auction.
        events = [
            event
            for event in three_round_game
            if not (isinstance(event, RoundScored) and event.round == 2)
        ]
        round_ = project(events).rounds[1]
        assert round_.score is None
        assert round_.outcome is RoundOutcome.ALL_PASS
        assert round_.complete is True

    def test_a_contracted_round_without_a_score_has_no_outcome(self, three_round_game):
        # Made or failed is a scoring question, and scoring lives in the
        # engine. The projection says "unknown" rather than guessing.
        events = [
            event
            for event in three_round_game
            if not (isinstance(event, RoundScored) and event.round == 1)
        ]
        round_ = project(events).rounds[0]
        assert round_.outcome is None
        assert round_.complete is True

    def test_a_doubled_contract_keeps_its_multiplier(self, three_round_game):
        # D4's defect family, seen from the other side: the projection reads
        # the double off ``Auction``'s own bookkeeping rather than re-deriving
        # who was entitled to make it, so a seatless auction — which has no
        # teams to compare — cannot refuse a coinche that really happened.
        events = list(three_round_game)
        index = next(
            i
            for i, event in enumerate(events)
            if isinstance(event, BidMade) and event.round == 1 and event.seq == 4
        )
        # seq 4 was East's pass. Make it a coinche, let North surcoinche, and
        # pass the auction out from there — seqs 4 through 8, still gapless.
        tail = [
            DoubleBid(player=Position.EAST),
            RedoubleBid(player=Position.NORTH),
            PassBid(player=Position.WEST),
            PassBid(player=Position.SOUTH),
            PassBid(player=Position.EAST),
        ]
        events[index : index + 1] = [
            BidMade(
                round=1,
                seq=4 + offset,
                position=bid.player,
                bid=bid,
                think_ms=None,
                ts=TS,
            )
            for offset, bid in enumerate(tail)
        ]
        contract = project(events).rounds[0].contract
        assert contract.doubled_by is Position.EAST
        assert contract.redoubled_by is Position.NORTH
        assert contract.get_multiplier() == 4


class TestIncompleteRounds:
    def test_a_round_abandoned_mid_trick_keeps_its_partial_trick(
        self, three_round_game
    ):
        # Cut round 3 off after the second card of trick 7: tricks 1-6 stand
        # complete, trick 7 is half played, trick 8 never happened.
        def after_the_cut(event) -> bool:
            return isinstance(event, CardPlayed) and event.round == 3 and (
                event.trick,
                SEATS.index(event.position),
            ) > (7, SEATS.index(Position.WEST))

        events = [
            event
            for event in three_round_game
            if not after_the_cut(event)
            and not (isinstance(event, RoundScored) and event.round == 3)
        ]
        round_ = project(events).rounds[2]
        assert len(round_.tricks) == 6
        assert len(round_.current_trick) == 2
        assert round_.complete is False

    def test_a_round_whose_auction_never_finished_is_incomplete(self, three_round_game):
        events = [
            event
            for event in three_round_game
            if not (isinstance(event, BidMade) and event.round == 2 and event.seq == 4)
        ]
        round_ = project(events).rounds[1]
        assert Auction(bids=round_.auction).is_terminal() is False
        assert round_.complete is False

    def test_a_passed_out_round_that_somehow_has_plays_is_incomplete(
        self, three_round_game
    ):
        # Four passes and then cards: the producer lost track of the redeal.
        # Structurally foldable, so it is reported rather than refused.
        events = list(three_round_game)
        index = next(
            i
            for i, event in enumerate(events)
            if isinstance(event, RoundScored) and event.round == 2
        )
        events[index:index] = [
            CardPlayed(
                round=2, trick=1, position=position,
                card=Card(suit, Rank.SEVEN), derived=False, think_ms=None, ts=TS,
            )
            for position, suit in zip(Position, Suit, strict=True)
        ]
        assert project(events).rounds[1].complete is False


class TestAttachmentByNumber:
    def test_a_score_arriving_after_the_next_deal_still_lands(self, three_round_game):
        # D2: the observed state snapshot describes the last *completed*
        # round, so a score read during round N+1 carries round N. Attaching
        # by position in the file would file it under the wrong round.
        late = next(
            event
            for event in three_round_game
            if isinstance(event, RoundScored) and event.round == 1
        )
        events = [
            event
            for event in three_round_game
            if not (isinstance(event, RoundScored) and event.round == 1)
        ]
        # Re-file it just before the end of the game, i.e. three deals after
        # the round it describes.
        events.insert(-1, late)
        game = project(events)
        assert game.rounds[0].score is late

    def test_an_event_for_a_round_never_dealt_is_refused(self, three_round_game):
        events = list(three_round_game)
        # Before ``game_ended``, so this trips the round lookup rather than
        # the "events after the end" guard.
        events.insert(
            -1,
            BidMade(
                round=9, seq=1, position=Position.NORTH,
                bid=PassBid(player=Position.NORTH), think_ms=None, ts=TS,
            ),
        )
        with pytest.raises(RecordFormatError, match="round 9"):
            project(events)

    def test_two_scores_for_one_round_are_refused(self, three_round_game):
        events = list(three_round_game)
        again = next(
            event
            for event in three_round_game
            if isinstance(event, RoundScored) and event.round == 1
        )
        events.insert(-1, again)
        with pytest.raises(RecordFormatError, match="scored twice"):
            project(events)


class TestRefusals:
    def test_a_stream_without_a_header_is_refused(self, three_round_game):
        with pytest.raises(RecordFormatError, match="header"):
            project(three_round_game[1:])

    def test_an_empty_stream_is_refused(self):
        with pytest.raises(RecordFormatError, match="header"):
            project([])

    def test_a_stream_without_game_started_is_refused(self, three_round_game):
        with pytest.raises(RecordFormatError, match="game_started"):
            project([three_round_game[0], *three_round_game[2:]])

    def test_a_stream_that_stops_after_the_header_is_refused(self, three_round_game):
        with pytest.raises(RecordFormatError, match="game_started"):
            project([three_round_game[0]])

    def test_a_second_header_is_refused(self, three_round_game):
        events = list(three_round_game)
        events.insert(-1, three_round_game[0])
        with pytest.raises(RecordFormatError, match="one header"):
            project(events)

    def test_a_second_game_started_is_refused(self, three_round_game):
        events = list(three_round_game)
        events.insert(-1, three_round_game[1])
        with pytest.raises(RecordFormatError, match="one header"):
            project(events)

    def test_a_second_game_ended_is_refused(self, three_round_game):
        with pytest.raises(RecordFormatError, match="game_ended"):
            project([*three_round_game, three_round_game[-1]])

    def test_a_duplicated_round_number_is_refused(
        self, three_round_game, dealt_hands
    ):
        events = list(three_round_game)
        events.insert(
            -1,
            RoundDealt(
                round=1, dealer=Position.NORTH, hands=dealt_hands,
                hands_derivation=HandsDerivation.SELF_PLAY, ts=TS,
            ),
        )
        with pytest.raises(RecordFormatError, match="twice"):
            project(events)

    def test_events_after_the_end_are_refused(self, three_round_game):
        events = [
            *three_round_game,
            BidMade(
                round=3, seq=9, position=Position.NORTH,
                bid=PassBid(player=Position.NORTH), think_ms=None, ts=TS,
            ),
        ]
        with pytest.raises(RecordFormatError, match="game_ended"):
            project(events)

    def test_a_gapped_bid_sequence_is_refused(self, three_round_game):
        # §4.2 says ``seq`` is gapless in the record; the wire's own
        # numbering is not, and renumbering is the parser's job.
        events = [
            event
            for event in three_round_game
            if not (isinstance(event, BidMade) and event.round == 1 and event.seq == 2)
        ]
        with pytest.raises(RecordFormatError, match="gapless"):
            project(events)

    def test_a_short_trick_is_refused(self, three_round_game):
        events = [
            event
            for event in three_round_game
            if not (
                isinstance(event, CardPlayed)
                and event.round == 1
                and event.trick == 3
                and event.position is Position.SOUTH
            )
        ]
        with pytest.raises(RecordFormatError, match="four plays"):
            project(events)

    def test_a_resumed_trick_is_refused(self, three_round_game):
        # Trick 2 reappearing after trick 3 started means the producer's
        # trick counter went backwards.
        events = list(three_round_game)
        index = next(
            i
            for i, event in enumerate(events)
            if isinstance(event, CardPlayed)
            and event.round == 1
            and event.trick == 4
            and event.position is Position.NORTH
        )
        events[index] = dataclasses.replace(events[index], trick=2)
        with pytest.raises(RecordFormatError, match="resumes"):
            project(events)

    def test_a_half_derived_trick_is_refused(self, three_round_game):
        # Trick 8 is reconstructed whole or observed whole; a mixture means
        # the producer lost track of which it was doing.
        events = [
            dataclasses.replace(event, derived=False)
            if (
                isinstance(event, CardPlayed)
                and event.round == 1
                and event.trick == 8
                and event.position is Position.NORTH
            )
            else event
            for event in three_round_game
        ]
        with pytest.raises(RecordFormatError, match="derived"):
            project(events)


class TestRoundBuilder:
    def test_a_round_refuses_an_event_it_cannot_hold(self, three_round_game):
        # ``project`` narrows before it files an event, so this guard is
        # unreachable through the public API. It is tested directly because
        # its job is to make a ninth event type announce itself rather than
        # be silently dropped.
        dealt = next(e for e in three_round_game if isinstance(e, RoundDealt))
        builder = _RoundBuilder(dealt=dealt)
        with pytest.raises(RecordFormatError, match="cannot hold"):
            builder.add(three_round_game[0])


class TestLoadGame:
    def test_reads_a_file_off_disk(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            for event in three_round_game:
                writer.write(event)
        game = load_game(path)
        assert game.complete is True
        assert len(game.rounds) == 3

    def test_carries_the_truncation_flag_through(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            for event in three_round_game:
                writer.write(event)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"event": "game_en')
        game = load_game(path)
        assert game.truncated is True
        assert game.complete is False
