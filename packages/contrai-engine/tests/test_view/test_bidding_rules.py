"""Tests for the auction-legality helpers in
:mod:`contrai_engine.view.bidding_rules`.

These messaging-only mirrors of the auction rules drive the adaptive
bid-prompt hint (which actions are legal for the next bidder) and the
specific nudge shown when a human types an illegal bid.
"""

from __future__ import annotations

from contrai_core import Auction, Suit
from contrai_core.bid import ContractBid, DoubleBid, PassBid, SlamLevel
from contrai_engine.view.bidding_rules import (
    _bid_to_legacy,
    _double_available_to,
    _illegal_bid_reason,
    _min_legal_contract_value,
    _redouble_available_to,
)


class TestBidToLegacy:
    def test_pass(self):
        assert _bid_to_legacy(PassBid(player=None)) == "Pass"

    def test_double(self):
        assert _bid_to_legacy(DoubleBid(player=None)) == "Double"

    def test_contract(self, four_players):
        north, *_ = four_players
        bid = ContractBid(north, 100, Suit.HEARTS)
        assert _bid_to_legacy(bid) == (100, Suit.HEARTS)


class TestRedoubleAvailability:
    """Validates the helper that drives the '(pass / redouble)' hint."""

    def test_empty_history_no_redouble(self, four_players):
        north, *_ = four_players
        assert _redouble_available_to([], north) is False

    def test_after_contract_only_no_redouble(self, four_players):
        """A bare contract bid hasn't been doubled yet."""
        north, _east, _south, _west = four_players
        history = [(north, (100, Suit.HEARTS))]
        assert _redouble_available_to(history, north) is False

    def test_contractor_can_redouble_after_opponent_doubles(
        self, four_players
    ):
        """N bid 100♥, E doubled. N (contractor) is up — must offer
        redouble."""
        north, east, south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
        ]
        assert _redouble_available_to(history, north) is True
        # Contractor's partner (South) is also on the contracting team.
        assert _redouble_available_to(history, south) is True

    def test_opponent_cannot_redouble(self, four_players):
        """An opponent of the contractor cannot redouble even when a
        Double is on the table."""
        north, east, _south, west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
        ]
        # West is on East's team → not the contracting team.
        assert _redouble_available_to(history, west) is False

    def test_pass_after_double_closes_window(self, four_players):
        """Once any player has passed after the Double, the redouble
        window has closed."""
        north, east, south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
            (south, "Pass"),
        ]
        # North is the only contracting-team member who hasn't acted —
        # but their PARTNER (S) already passed. By bidding-loop rules
        # the redouble window is closed once a pass intervenes.
        assert _redouble_available_to(history, north) is False

    def test_already_redoubled_no_more(self, four_players):
        north, east, south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
            (south, "Redouble"),
        ]
        assert _redouble_available_to(history, north) is False


class TestDoubleAvailability:
    """Validates the helper that gates the 'double' hint."""

    def test_empty_history_no_double(self, four_players):
        north, *_ = four_players
        assert _double_available_to([], north) is False

    def test_only_passes_no_double(self, four_players):
        north, east, south, _west = four_players
        history = [(east, "Pass"), (south, "Pass")]
        assert _double_available_to(history, north) is False

    def test_opponent_contract_is_doublable(self, four_players):
        """South may double East's standing contract."""
        _north, east, south, _west = four_players
        history = [(east, (90, Suit.SPADES))]
        assert _double_available_to(history, south) is True

    def test_own_side_contract_not_doublable(self, four_players):
        """South may NOT double North's (partner's) contract."""
        north, _east, south, _west = four_players
        history = [(north, (90, Suit.SPADES))]
        assert _double_available_to(history, south) is False

    def test_passes_do_not_close_double_window(self, four_players):
        """Intervening passes keep the Coinche window open."""
        _north, east, south, west = four_players
        history = [(east, (90, Suit.SPADES)), (west, "Pass")]
        assert _double_available_to(history, south) is True

    def test_already_doubled_not_doublable_again(self, four_players):
        north, east, south, _west = four_players
        history = [(east, (90, Suit.SPADES)), (south, "Double")]
        # North is on the contracting side's opponents... but a Double
        # already stands, so no further Double is legal regardless.
        assert _double_available_to(history, north) is False


class TestMinLegalContractValue:
    """The dynamic floor that drives the prompt's worked example."""

    def test_empty_history_opens_at_floor(self, four_players):
        """Nothing bid yet → the ladder opens at 80."""
        assert _min_legal_contract_value([]) == 80

    def test_only_passes_still_floor(self, four_players):
        """Passes don't raise the floor — still 80."""
        north, east, _south, _west = four_players
        history = [(north, "Pass"), (east, "Pass")]
        assert _min_legal_contract_value(history) == 80

    def test_next_step_above_standing_contract(self, four_players):
        """90 standing → cheapest legal raise is 100."""
        north, _east, _south, _west = four_players
        history = [(north, (90, Suit.DIAMONDS))]
        assert _min_legal_contract_value(history) == 100

    def test_uses_highest_not_latest_shape(self, four_players):
        """The most recent contract is the highest (monotonic), so the
        floor sits one step above it."""
        north, east, _south, _west = four_players
        history = [
            (east, (80, Suit.HEARTS)),
            (north, (120, Suit.CLUBS)),
        ]
        assert _min_legal_contract_value(history) == 130

    def test_double_does_not_reset_floor(self, four_players):
        """A trailing Double leaves the standing contract intact, so the
        floor is still computed from the last numeric bid."""
        north, east, _south, _west = four_players
        history = [(north, (110, Suit.SPADES)), (east, "Double")]
        assert _min_legal_contract_value(history) == 120

    def test_180_leaves_no_numeric_raise(self, four_players):
        """Past 180 only the Slam sentinels remain → None."""
        north, _east, _south, _west = four_players
        history = [(north, (180, Suit.HEARTS))]
        assert _min_legal_contract_value(history) is None

    def test_slam_outranked_by_nothing(self, four_players):
        """A standing Slam blocks every further contract bid → None."""
        north, _east, _south, _west = four_players
        history = [(north, (SlamLevel.SLAM, Suit.HEARTS))]
        assert _min_legal_contract_value(history) is None


class TestIllegalBidReason:
    """The specific nudge shown when a human types an illegal bid."""

    def _auction(self, bids):
        auction = Auction.empty()
        for bid in bids:
            auction = auction.apply(bid)
        return auction

    def test_double_own_partner(self, four_players):
        north, east, south, west = four_players
        auction = self._auction(
            [PassBid(east), ContractBid(north, 90, Suit.SPADES), PassBid(west)]
        )
        reason = _illegal_bid_reason(DoubleBid(south), auction)
        assert "own side" in reason

    def test_double_with_no_contract(self, four_players):
        north, east, _south, _west = four_players
        auction = self._auction([PassBid(east)])
        reason = _illegal_bid_reason(DoubleBid(north), auction)
        assert "no contract" in reason.lower()

    def test_double_already_doubled(self, four_players):
        north, east, south, _west = four_players
        auction = self._auction(
            [ContractBid(east, 90, Suit.SPADES), DoubleBid(south)]
        )
        reason = _illegal_bid_reason(DoubleBid(north), auction)
        assert "already" in reason.lower()

    def test_contract_must_outrank(self, four_players):
        _north, east, south, _west = four_players
        auction = self._auction([ContractBid(east, 100, Suit.SPADES)])
        reason = _illegal_bid_reason(
            ContractBid(south, 80, Suit.HEARTS), auction
        )
        assert "outrank" in reason and "100" in reason
