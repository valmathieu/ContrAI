"""Tests for the bidding screen in :mod:`contrai_engine.view.screens.bidding`.

Covers the branchy parts: the latest-bid-per-seat collapse in the
bidding diamond, the line-break-every-four layout of the history panel,
and — most importantly — the adaptive prompt whose hints are derived
from :meth:`Auction.legal_actions` (cheapest legal raise, the
double/redouble branches, the past-180 Slam-only tail).
"""

from __future__ import annotations

from contrai_core import Auction, Suit
from contrai_core.bid import ContractBid, DoubleBid, PassBid

from contrai_engine.view.screens.bidding import (
    _ai_bid_announcement,
    _bidding_prompt_text,
    _panel_bidding_history,
    _render_bidding_diamond,
)


class TestRenderBiddingDiamond:
    """Each seat shows its *latest* bid; pending shows ?, un-bid shows ·."""

    def test_later_bid_by_same_seat_overwrites_earlier(self, four_players):
        north, east, south, west = four_players
        history = [
            ContractBid(north, 80, Suit.HEARTS),
            PassBid(east),
            ContractBid(south, 100, Suit.HEARTS),
            PassBid(west),
            ContractBid(north, 110, Suit.HEARTS),
        ]
        text = _render_bidding_diamond(
            history, pending_position=None, width=42
        ).plain
        assert "N 110 ♥" in text
        assert "80" not in text  # overwritten by North's later raise
        assert "S 100 ♥" in text
        assert "W Pass" in text

    def test_pending_seat_shows_question_mark_over_its_bid(
        self, four_players
    ):
        north, east, *_ = four_players
        history = [ContractBid(north, 80, Suit.HEARTS), PassBid(east)]
        text = _render_bidding_diamond(
            history, pending_position="East", width=42
        ).plain
        # East already passed, but as the seat about to act it reads "?".
        assert "E ?" in text
        assert "E Pass" not in text

    def test_seats_without_a_bid_show_a_dot(self, four_players):
        north, *_ = four_players
        history = [ContractBid(north, 80, Suit.HEARTS)]
        text = _render_bidding_diamond(
            history, pending_position="East", width=42
        ).plain
        assert "S ·" in text
        assert "W ·" in text


class TestPanelBiddingHistory:
    """Chronological history: #N gutter every four bids, fixed lanes."""

    def test_empty_history_shows_placeholder(self):
        text = _panel_bidding_history([]).renderable.plain
        assert "(no bids yet)" in text

    def test_breaks_line_every_four_bids_with_round_gutters(
        self, four_players
    ):
        north, east, south, west = four_players
        bids = [
            ContractBid(south, 80, Suit.HEARTS),
            PassBid(west),
            PassBid(north),
            ContractBid(east, 90, Suit.SPADES),
            ContractBid(south, 100, Suit.HEARTS),
        ]
        text = _panel_bidding_history(bids).renderable.plain
        lines = text.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("#1")
        assert lines[1].startswith("#2")
        # The fifth bid opens round #2 on its own line.
        assert "S 100 ♥" in lines[1]


class TestBiddingPromptText:
    """The adaptive hint only advertises actions that are legal."""

    def test_fresh_auction_advertises_the_80_floor_and_no_double(
        self, four_players
    ):
        *_ , south, _west = four_players
        text = _bidding_prompt_text(Auction.empty(), south).plain
        assert "'80 H'" in text
        assert "'pass'" in text
        assert "double" not in text  # nothing to double yet

    def test_worked_example_tracks_the_cheapest_legal_raise(
        self, four_players
    ):
        _north, _east, south, west = four_players
        auction = Auction.empty().apply(ContractBid(south, 90, Suit.HEARTS))
        text = _bidding_prompt_text(auction, west).plain
        # 90 stands: the example shows 100, never the bare 80 floor.
        assert "S bid 90 ♥" in text
        assert "'100 H'" in text
        assert "'80 H'" not in text
        assert "'double'" in text  # opponent contract → double is legal

    def test_no_double_hint_against_your_own_sides_contract(
        self, four_players
    ):
        north, _east, south, _west = four_players
        auction = Auction.empty().apply(ContractBid(south, 100, Suit.HEARTS))
        text = _bidding_prompt_text(auction, north).plain
        assert "'110 H'" in text
        assert "double" not in text  # can't double your partner

    def test_doubled_contract_offers_pass_or_redouble_only(
        self, four_players
    ):
        north, _east, south, west = four_players
        auction = (
            Auction.empty()
            .apply(ContractBid(south, 100, Suit.HEARTS))
            .apply(DoubleBid(west))
        )
        text = _bidding_prompt_text(auction, north).plain
        assert "W doubled." in text
        assert "(pass / redouble)" in text

    def test_past_180_drops_the_numeric_example(self, four_players):
        _north, east, south, _west = four_players
        auction = Auction.empty().apply(ContractBid(east, 180, Suit.HEARTS))
        text = _bidding_prompt_text(auction, south).plain
        # Only Slam-family raises remain; they are filtered from the
        # worked example, leaving pass (and the legal double).
        assert "'pass'" in text
        assert "'double'" in text
        assert " H'" not in text


class TestAiBidAnnouncement:
    """The post-bid pause line for each bid kind."""

    def test_pass(self, four_players):
        north, *_ = four_players
        assert _ai_bid_announcement(north, PassBid(north)).plain == "N passes."

    def test_contract(self, four_players):
        _north, east, *_ = four_players
        text = _ai_bid_announcement(
            east, ContractBid(east, 100, Suit.HEARTS)
        ).plain
        assert text == "E bids 100 ♥."

    def test_double(self, four_players):
        *_, west = four_players
        assert _ai_bid_announcement(west, DoubleBid(west)).plain == "W doubles."
