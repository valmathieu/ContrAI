"""Tests for the illegal-bid nudge in
:mod:`contrai_engine.view.bidding_rules`.

The single remaining helper, :func:`_illegal_bid_reason`, is a
messaging-only mirror of the auction rules: it builds the specific
explanation shown when a human types an illegal bid. The authoritative
verdict is :meth:`Auction.is_legal`, and the adaptive prompt hint is now
derived directly from :meth:`Auction.legal_actions` (covered by
``test_rich_view``'s ``TestBiddingPromptHint``).
"""

from __future__ import annotations

from contrai_core import Auction, Suit
from contrai_core.bid import ContractBid, DoubleBid, PassBid
from contrai_engine.view.bidding_rules import _illegal_bid_reason


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
