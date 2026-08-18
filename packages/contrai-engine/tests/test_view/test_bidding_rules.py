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

import pytest

from contrai_core import AllTrumpBelote, Auction, RuleConfig, Suit, TrumpVariant
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

    @pytest.mark.parametrize("variant, name", [
        (TrumpVariant.NO_TRUMP, "no trump"),
        (TrumpVariant.ALL_TRUMP, "all trump"),
    ])
    def test_explains_a_trump_choice_the_table_does_not_offer(
        self, four_players, variant, name
    ):
        north, *_ = four_players
        reason = _illegal_bid_reason(ContractBid(north, 80, variant), Auction())
        assert name in reason.lower()

    @pytest.mark.parametrize("variant, value, top", [
        # No trump stops at 160 whatever the table; all trump follows the
        # belote regime, which defaults to `single` and so stops at 180.
        (TrumpVariant.NO_TRUMP, 170, "160"),
        (TrumpVariant.ALL_TRUMP, 190, "180"),
    ])
    def test_explains_a_value_above_the_mode_ladder(
        self, four_players, variant, value, top
    ):
        north, *_ = four_players
        rules = RuleConfig(extended_trump_choices=True)
        reason = _illegal_bid_reason(
            ContractBid(north, value, variant), Auction.empty(rules=rules)
        )
        assert top in reason

    def test_the_ladder_message_names_the_regime_ceiling(self, four_players):
        # The same 190 all-trump bid is legal at a `four` table and over
        # the ceiling at a `none` one — the message has to read the knob.
        north, *_ = four_players
        rules = RuleConfig(extended_trump_choices=True,
                           all_trump_belote=AllTrumpBelote.NONE)
        reason = _illegal_bid_reason(
            ContractBid(north, 190, TrumpVariant.ALL_TRUMP),
            Auction.empty(rules=rules),
        )
        assert "160" in reason

    def test_the_ladder_message_beats_the_outrank_message(self, four_players):
        # Both reasons apply to a 190 heart bid over a standing 100: the
        # ladder cap is the more specific and more useful one.
        _north, east, south, _west = four_players
        rules = RuleConfig(extended_trump_choices=True)
        auction = Auction.empty(rules=rules).apply(
            ContractBid(east, 100, Suit.SPADES)
        )
        reason = _illegal_bid_reason(
            ContractBid(south, 190, Suit.HEARTS), auction
        )
        assert "180" in reason
