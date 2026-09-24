"""Pins ``RoundScript`` addressing and ``RecordedPlayer``'s two hooks.

The addressing is the part worth testing hard. A seat popping a private
queue would be simpler and would be wrong — the engine auto-applies a bid
whenever a seat has one legal action, without consulting that seat, so a
queue falls behind the first time it is skipped. Indexing off the
auction's own length cannot.
"""

from __future__ import annotations

import dataclasses
import random

import pytest
from contrai_core import (
    Auction,
    Card,
    ContractBid,
    DoubleBid,
    ObservedPlay,
    PassBid,
    Position,
    Rank,
    RuleConfig,
    Suit,
    TrickRecord,
)

from contrai_engine.model.player.rationale import (
    BidDecision,
    CardDecision,
    Rationale,
)
from contrai_engine.replay import (
    RecordedPlayer,
    RoundScript,
    ScriptExhaustedError,
    SeatMismatchError,
)
from contrai_engine.replay.player import ReplayedBid, ReplayedCard


# ---------------------------------------------------------------------------
# Stand-ins: the two shapes ``RoundScript.of`` and the hooks read.
# ---------------------------------------------------------------------------


class _Round:
    """The three ``RoundRecord`` attributes ``RoundScript.of`` reads."""

    def __init__(self, number=1, auction=(), tricks=(), current_trick=()):
        self.number = number
        self.auction = auction
        self.tricks = tricks
        self.current_trick = current_trick


class _Observation:
    """The two ``PlayObservation`` attributes ``choose_card`` reads."""

    def __init__(self, completed_tricks=(), current_trick=()):
        self.completed_tricks = completed_tricks
        self.current_trick = current_trick


def _trick(*pairs) -> TrickRecord:
    """A completed trick from four ``(seat, card)`` pairs."""

    return TrickRecord(ObservedPlay(seat, card) for seat, card in pairs)


SPADE_7 = Card(Suit.SPADES, Rank.SEVEN)
SPADE_8 = Card(Suit.SPADES, Rank.EIGHT)
SPADE_9 = Card(Suit.SPADES, Rank.NINE)
SPADE_10 = Card(Suit.SPADES, Rank.TEN)
HEART_7 = Card(Suit.HEARTS, Rank.SEVEN)


# ---------------------------------------------------------------------------
# RoundScript
# ---------------------------------------------------------------------------


class TestRoundScriptOf:
    def test_it_takes_the_auction_verbatim(self):
        bids = (
            ContractBid(Position.NORTH, 80, Suit.SPADES),
            PassBid(Position.WEST),
        )

        script = RoundScript.of(_Round(number=4, auction=bids))

        assert script.number == 4
        assert script.bids == bids

    def test_it_flattens_the_tricks_into_play_order(self):
        first = _trick(
            (Position.NORTH, SPADE_7),
            (Position.WEST, SPADE_8),
            (Position.SOUTH, SPADE_9),
            (Position.EAST, SPADE_10),
        )
        second = _trick(
            (Position.EAST, HEART_7),
            (Position.NORTH, SPADE_7),
            (Position.WEST, SPADE_8),
            (Position.SOUTH, SPADE_9),
        )

        script = RoundScript.of(_Round(tricks=(first, second)))

        assert len(script.plays) == 8
        assert script.plays[:4] == tuple(first)
        assert script.plays[4:] == tuple(second)

    def test_a_trailing_partial_trick_is_kept(self):
        # A round abandoned mid-trick still played the cards it played.
        partial = (ObservedPlay(Position.NORTH, SPADE_7),)

        script = RoundScript.of(_Round(current_trick=partial))

        assert script.plays == partial

    def test_a_passed_out_round_scripts_no_plays(self):
        script = RoundScript.of(
            _Round(auction=tuple(PassBid(seat) for seat in Position))
        )

        assert script.plays == ()
        assert len(script.bids) == 4


class TestBidAt:
    """Bids are addressed per seat, by that seat's own count."""

    SCRIPT = RoundScript(
        number=2,
        bids=(
            ContractBid(Position.NORTH, 80, Suit.SPADES),
            PassBid(Position.WEST),
            DoubleBid(Position.SOUTH),
            ContractBid(Position.NORTH, 90, Suit.HEARTS),
        ),
    )

    def test_it_counts_only_the_seat_s_own_bids(self):
        # North's second bid is the 90, not the auction's second entry.
        assert self.SCRIPT.bid_at(1, Position.NORTH).value == 90

    def test_the_first_of_a_seat_s_bids_is_ordinal_zero(self):
        assert self.SCRIPT.bid_at(0, Position.WEST) == PassBid(Position.WEST)

    def test_it_keeps_the_bid_seated_on_its_position(self):
        bid = self.SCRIPT.bid_at(0, Position.NORTH)

        assert bid.player is Position.NORTH

    def test_a_seat_that_never_bid_is_exhausted_at_once(self):
        with pytest.raises(ScriptExhaustedError) as excinfo:
            self.SCRIPT.bid_at(0, Position.EAST)

        assert "East" in str(excinfo.value)
        assert "0 for that seat" in str(excinfo.value)

    def test_running_past_a_seat_s_own_bids_is_refused(self):
        with pytest.raises(ScriptExhaustedError) as excinfo:
            self.SCRIPT.bid_at(2, Position.NORTH)

        assert "bid 3 of its own" in str(excinfo.value)
        assert "Round 2" in str(excinfo.value)


class TestPlayAt:
    SCRIPT = RoundScript(
        number=3,
        plays=(
            ObservedPlay(Position.NORTH, SPADE_7),
            ObservedPlay(Position.WEST, SPADE_8),
        ),
    )

    def test_it_returns_the_card_at_that_index(self):
        assert self.SCRIPT.play_at(1, Position.WEST) == SPADE_8

    def test_a_seat_mismatch_is_refused(self):
        with pytest.raises(SeatMismatchError) as excinfo:
            self.SCRIPT.play_at(0, Position.SOUTH)

        assert "North" in str(excinfo.value)

    def test_running_past_the_record_is_refused(self):
        with pytest.raises(ScriptExhaustedError) as excinfo:
            self.SCRIPT.play_at(2, Position.SOUTH)

        assert "play 3" in str(excinfo.value)


# ---------------------------------------------------------------------------
# RecordedPlayer
# ---------------------------------------------------------------------------


class TestRecordedPlayer:
    @staticmethod
    def _seated(script: RoundScript, seat=Position.NORTH) -> RecordedPlayer:
        player = RecordedPlayer("N", seat)
        player.script = script
        return player

    def test_it_is_never_a_human_seat(self):
        # The engine routes a human's turn through the view; a replayed
        # seat must take the ``choose_*`` path like any AI.
        assert RecordedPlayer("N", Position.NORTH).is_human is False

    def test_choose_bid_counts_the_seat_s_own_bids_in_the_auction(self):
        script = RoundScript(
            number=1,
            bids=(
                ContractBid(Position.NORTH, 80, Suit.SPADES),
                PassBid(Position.WEST),
                ContractBid(Position.NORTH, 90, Suit.HEARTS),
            ),
        )
        player = self._seated(script)
        auction = Auction(bids=script.bids[:2])

        decision = player.choose_bid(auction)

        assert decision.bid.value == 90
        assert decision.bid.suit is Suit.HEARTS

    def test_choose_bid_re_seats_the_bid_onto_the_live_player(self):
        # Everything downstream — the contract, the doubling side, the
        # scorer — reads a live player off the bid, not a Position.
        script = RoundScript(
            number=1, bids=(ContractBid(Position.NORTH, 80, Suit.SPADES),)
        )
        player = self._seated(script)

        decision = player.choose_bid(Auction())

        assert decision.bid.player is player

    def test_choose_bid_carries_a_rationale_naming_the_record(self):
        script = RoundScript(
            number=1, bids=(ContractBid(Position.NORTH, 80, Suit.SPADES),)
        )

        decision = self._seated(script).choose_bid(Auction())

        assert decision.rationale.rule == "recorded action"
        assert "North" in decision.rationale.detail

    def test_a_bid_the_engine_auto_applied_still_advances_the_seat(self):
        # South was auto-passed at index 2 (it could only pass), then is
        # consulted at index 4 once East's double gives it the redouble.
        # The auto-applied pass is in the auction, so South's ordinal is
        # 1 and it gets its *second* recorded bid — a FIFO queue that the
        # auto-pass never touched would hand back the pass again.
        script = RoundScript(
            number=1,
            bids=(
                ContractBid(Position.NORTH, 80, Suit.SPADES),
                PassBid(Position.WEST),
                PassBid(Position.SOUTH),
                DoubleBid(Position.EAST),
                ContractBid(Position.SOUTH, 90, Suit.HEARTS),
            ),
        )
        player = self._seated(script, Position.SOUTH)

        decision = player.choose_bid(Auction(bids=script.bids[:4]))

        assert decision.bid.value == 90

    def test_a_forced_pass_the_record_omits_does_not_shift_a_seat(self):
        # The observed shape: the site never transmits East's forced pass
        # after West's double, so the record holds four bids where the
        # replayed auction holds five. North's own count is what keeps
        # the two in step — it is 1 either way, so North still gets its
        # recorded pass rather than running off the end.
        script = RoundScript(
            number=1,
            bids=(
                ContractBid(Position.NORTH, 80, Suit.SPADES),
                DoubleBid(Position.WEST),
                PassBid(Position.SOUTH),
                PassBid(Position.NORTH),
            ),
        )
        player = self._seated(script, Position.NORTH)
        replayed = Auction(
            bids=(
                script.bids[0],
                script.bids[1],
                script.bids[2],
                PassBid(Position.EAST),  # forced, engine-inserted
            )
        )

        decision = player.choose_bid(replayed)

        assert isinstance(decision.bid, PassBid)
        assert decision.bid.player is player

    def test_choose_card_counts_four_per_completed_trick(self):
        played = _trick(
            (Position.NORTH, SPADE_7),
            (Position.WEST, SPADE_8),
            (Position.SOUTH, SPADE_9),
            (Position.EAST, SPADE_10),
        )
        script = RoundScript(
            number=1,
            plays=tuple(played) + (ObservedPlay(Position.EAST, HEART_7),),
        )
        player = self._seated(script, Position.EAST)

        decision = player.choose_card(_Observation(completed_tricks=(played,)))

        assert decision.card == HEART_7

    def test_choose_card_counts_the_trick_in_progress_too(self):
        script = RoundScript(
            number=1,
            plays=(
                ObservedPlay(Position.NORTH, SPADE_7),
                ObservedPlay(Position.WEST, SPADE_8),
            ),
        )
        player = self._seated(script, Position.WEST)

        decision = player.choose_card(
            _Observation(current_trick=(ObservedPlay(Position.NORTH, SPADE_7),))
        )

        assert decision.card == SPADE_8

    def test_choose_card_carries_a_rationale_naming_the_record(self):
        script = RoundScript(
            number=1, plays=(ObservedPlay(Position.NORTH, SPADE_7),)
        )

        decision = self._seated(script).choose_card(_Observation())

        assert decision.rationale.rule == "recorded action"

    def test_a_seat_with_no_script_refuses_to_bid(self):
        player = RecordedPlayer("N", Position.NORTH)

        with pytest.raises(ScriptExhaustedError) as excinfo:
            player.choose_bid(Auction())

        assert "no round scripted" in str(excinfo.value)

    def test_a_seat_with_no_script_refuses_to_play(self):
        player = RecordedPlayer("N", Position.NORTH)

        with pytest.raises(ScriptExhaustedError):
            player.choose_card(_Observation())


# ---------------------------------------------------------------------------
# Explaining an AI seat
# ---------------------------------------------------------------------------


def _strategies(*, bid=None, card=None, drawn_from=(), draws=False):
    """A ``(bidding, cardplay)`` factory pair answering fixed decisions.

    Args:
        bid: The bid the stand-in strategy would make; seated on the
            player it is built onto.
        card: The card it would play.
        drawn_from: The rationale's ``drawn_from`` for that card.
        draws: Whether answering consumes the global RNG, the way the
            expert card play's tie-break does.
    """

    built: list = []

    class _Strategy:
        def __init__(self, player):
            self.player = player
            built.append(player)

        def choose_bid(self, auction):
            if draws:
                random.random()
            return BidDecision(
                dataclasses.replace(bid, player=self.player),
                Rationale("open on strength", "the strategy's own words."),
            )

        def choose_card(self, observation):
            if draws:
                random.random()
            return CardDecision(
                card,
                Rationale(
                    "concede cheaply",
                    "the strategy's own words.",
                    drawn_from=drawn_from,
                ),
            )

    return (_Strategy, _Strategy), built


class TestExplainedPlayer:
    """A seat given its strategy explains each recorded action with it."""

    @staticmethod
    def _seated(script, strategies, seat=Position.NORTH):
        player = RecordedPlayer("N", seat, strategies=strategies)
        player.script = script
        return player

    BID_SCRIPT = RoundScript(
        number=1, bids=(ContractBid(Position.NORTH, 80, Suit.SPADES),)
    )
    CARD_SCRIPT = RoundScript(
        number=1, plays=(ObservedPlay(Position.NORTH, SPADE_7),)
    )

    def test_the_strategies_are_built_onto_the_seat_itself(self):
        # So they read this seat's hand, position and team — the view the
        # seat really had.
        strategies, built = _strategies(card=SPADE_7)

        player = RecordedPlayer("N", Position.NORTH, strategies=strategies)

        assert built == [player, player]

    def test_an_agreeing_bid_carries_the_strategy_s_rationale(self):
        strategies, _ = _strategies(bid=ContractBid(None, 80, Suit.SPADES))
        player = self._seated(self.BID_SCRIPT, strategies)

        decision = player.choose_bid(Auction())

        assert isinstance(decision, ReplayedBid)
        assert decision.rationale.rule == "open on strength"
        assert decision.preferred is None
        # The recorded bid is what is played, re-seated as ever.
        assert decision.bid.player is player
        assert decision.bid.value == 80

    def test_a_bid_the_strategy_would_not_make_names_its_own(self):
        strategies, _ = _strategies(bid=ContractBid(None, 100, Suit.HEARTS))
        player = self._seated(self.BID_SCRIPT, strategies)

        decision = player.choose_bid(Auction())

        assert decision.bid.value == 80
        assert "100" in decision.preferred

    def test_an_agreeing_card_carries_the_strategy_s_rationale(self):
        strategies, _ = _strategies(card=SPADE_7)
        player = self._seated(self.CARD_SCRIPT, strategies)

        decision = player.choose_card(_Observation())

        assert isinstance(decision, ReplayedCard)
        assert decision.card == SPADE_7
        assert decision.rationale.rule == "concede cheaply"
        assert decision.preferred is None

    def test_a_card_the_strategy_would_not_play_names_its_own(self):
        strategies, _ = _strategies(card=SPADE_9)
        player = self._seated(self.CARD_SCRIPT, strategies)

        decision = player.choose_card(_Observation())

        assert decision.card == SPADE_7
        assert decision.preferred == str(SPADE_9)

    def test_a_card_among_the_strategy_s_random_draw_agrees(self):
        # Asked again, the strategy drew the other 7; the recorded one
        # was just as likely, so this is no disagreement.
        strategies, _ = _strategies(
            card=HEART_7, drawn_from=(str(SPADE_7), str(HEART_7))
        )
        player = self._seated(self.CARD_SCRIPT, strategies)

        decision = player.choose_card(_Observation())

        assert decision.preferred is None
        assert decision.rationale.drawn_from == (str(SPADE_7), str(HEART_7))

    def test_asking_leaves_the_global_rng_where_it_was(self):
        strategies, _ = _strategies(
            bid=ContractBid(None, 80, Suit.SPADES), card=SPADE_7, draws=True
        )
        player = self._seated(
            RoundScript(
                number=1,
                bids=self.BID_SCRIPT.bids,
                plays=self.CARD_SCRIPT.plays,
            ),
            strategies,
        )
        random.seed(42)
        before = random.getstate()

        player.choose_bid(Auction())
        player.choose_card(_Observation())

        assert random.getstate() == before
