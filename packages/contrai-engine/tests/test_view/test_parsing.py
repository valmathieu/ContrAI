"""Tests for the human-input parsers in :mod:`contrai_engine.view.parsing`.

Humans type bids and card numbers at the prompt; these two parsers turn
that text into engine-shaped values (or ``None`` so the loops re-ask).
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Position, Rank, Suit, TrumpVariant
from contrai_core.bid import (
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    SlamLevel,
)
from contrai_engine.model.player import AiPlayer
from contrai_engine.view.parsing import _parse_bid_input, _parse_card_input


# ======================================================================
# _parse_bid_input
# ======================================================================


class TestParseBidInput:
    """Bid-string parser. Returns a :class:`Bid` or ``None`` on error.

    ``Bid`` equality is type + payload (the player is excluded), so the
    parsed bid compares equal to the expected variant regardless of which
    player instance it is attached to.
    """

    @pytest.fixture
    def player(self):
        """A player to attach the parsed bid to."""
        return AiPlayer("Bot", Position.SOUTH)

    @pytest.mark.parametrize("raw", ["pass", "PASS", "Pass", "p", " pass "])
    def test_pass_variants(self, raw, player):
        assert _parse_bid_input(raw, player) == PassBid(player)

    @pytest.mark.parametrize(
        "raw", ["double", "d", "Double", "DOUBLE", " double "]
    )
    def test_double_variants(self, raw, player):
        assert _parse_bid_input(raw, player) == DoubleBid(player)

    @pytest.mark.parametrize(
        "raw", ["redouble", "r", "Redouble", "REDOUBLE", " redouble "]
    )
    def test_redouble_variants(self, raw, player):
        assert _parse_bid_input(raw, player) == RedoubleBid(player)

    @pytest.mark.parametrize(
        "raw",
        ["coinche", "surcoinche", "contrée", "contree",
         "surcontrée", "surcontree", "passe"],
    )
    def test_rejects_french_aliases(self, raw, player):
        """The CLI uses the English vocabulary exclusively. The parser
        used to accept the French aliases ``coinche`` / ``surcoinche`` /
        ``contrée`` / ``surcontrée`` / ``passe``; those have been
        retired."""
        assert _parse_bid_input(raw, player) is None

    @pytest.mark.parametrize(
        "raw,value,suit",
        [
            ("80 h", 80, Suit.HEARTS),
            ("100 hearts", 100, Suit.HEARTS),
            ("100 heart", 100, Suit.HEARTS),
            ("90 s", 90, Suit.SPADES),
            ("110 spades", 110, Suit.SPADES),
            ("120 d", 120, Suit.DIAMONDS),
            ("130 diamond", 130, Suit.DIAMONDS),
            ("140 c", 140, Suit.CLUBS),
            ("150 clubs", 150, Suit.CLUBS),
            ("160 nt", 160, TrumpVariant.NO_TRUMP),
            ("160 notrump", 160, TrumpVariant.NO_TRUMP),
            ("160 at", 160, TrumpVariant.ALL_TRUMP),
            ("160 alltrump", 160, TrumpVariant.ALL_TRUMP),
            ("240 all-trump", 240, TrumpVariant.ALL_TRUMP),
            ("80 ♥", 80, Suit.HEARTS),
            ("80 ♠", 80, Suit.SPADES),
        ],
    )
    def test_contract_bid_separated(self, raw, value, suit, player):
        assert _parse_bid_input(raw, player) == ContractBid(player, value, suit)

    @pytest.mark.parametrize(
        "raw,value,suit",
        [
            ("100h", 100, Suit.HEARTS),
            ("80s", 80, Suit.SPADES),
            ("130c", 130, Suit.CLUBS),
            ("240at", 240, TrumpVariant.ALL_TRUMP),
        ],
    )
    def test_contract_bid_glued(self, raw, value, suit, player):
        """Value and suit may be glued together with no separator."""
        assert _parse_bid_input(raw, player) == ContractBid(player, value, suit)

    def test_parses_a_table_forbidden_bid_without_raising(self, player):
        # Syntactic validation only — legality is Auction.is_legal's call,
        # and the rejection message comes from _illegal_bid_reason. A 240
        # bid is well-formed whatever the table offers.
        bid = _parse_bid_input("240 s", player)
        assert bid == ContractBid(player, 240, Suit.SPADES)

    @pytest.mark.parametrize(
        "raw,suit",
        [
            ("slam s", Suit.SPADES),
            ("slam h", Suit.HEARTS),
            ("slam d", Suit.DIAMONDS),
            ("slam c", Suit.CLUBS),
            ("slams", Suit.SPADES),  # glued
            ("SLAM H", Suit.HEARTS),  # case-insensitive
        ],
    )
    def test_slam(self, raw, suit, player):
        assert _parse_bid_input(raw, player) == ContractBid(
            player, SlamLevel.SLAM, suit
        )

    @pytest.mark.parametrize(
        "raw,suit",
        [
            ("soloslam s", Suit.SPADES),
            ("solo slam h", Suit.HEARTS),  # two-word form
            ("solo slam d", Suit.DIAMONDS),
            ("soloslam c", Suit.CLUBS),
            ("soloslams", Suit.SPADES),  # glued
            ("SOLO SLAM H", Suit.HEARTS),  # case-insensitive
        ],
    )
    def test_solo_slam(self, raw, suit, player):
        assert _parse_bid_input(raw, player) == ContractBid(
            player, SlamLevel.SOLO_SLAM, suit
        )

    def test_capital_letters_in_value_suit(self, player):
        assert _parse_bid_input("100 H", player) == ContractBid(
            player, 100, Suit.HEARTS
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "",          # empty
            "  ",        # whitespace only
            "xyz",       # garbage
            "80",        # value but no suit
            "h",         # suit but no value
            "80 q",      # invalid suit letter
            "70 h",      # value below the 80 floor
            "85 h",      # value not on the 10-step ladder
            "250 h",     # value above the 240 ceiling
            "abc h",     # non-numeric value
            "80 h s",    # too many tokens
            "capot s",   # legacy name no longer accepted
            "160 sa",    # French sans-atout alias no longer accepted
        ],
    )
    def test_rejects_garbage(self, raw, player):
        assert _parse_bid_input(raw, player) is None


# ======================================================================
# _parse_card_input
# ======================================================================


class TestParseCardInput:
    """Card-number parser. Validates that the picked card is playable."""

    @pytest.fixture
    def hand(self):
        """Four-card display hand the parser indexes into."""
        return [
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.QUEEN),
        ]

    def test_valid_choice_in_playable(self, hand):
        playable = hand[:2]  # only hearts are playable
        assert _parse_card_input("1", hand, playable) is hand[0]
        assert _parse_card_input("2", hand, playable) is hand[1]

    def test_choice_not_in_playable(self, hand):
        """User picks a number that maps to a non-playable card."""
        playable = hand[:2]
        assert _parse_card_input("3", hand, playable) is None  # A♠ not playable

    def test_choice_out_of_range(self, hand):
        assert _parse_card_input("0", hand, hand) is None
        assert _parse_card_input("5", hand, hand) is None

    @pytest.mark.parametrize("raw", ["", "abc", "1.5", "-1", " 1a"])
    def test_non_digit(self, hand, raw):
        assert _parse_card_input(raw, hand, hand) is None

    def test_whitespace_trimmed(self, hand):
        assert _parse_card_input(" 1 ", hand, hand) is hand[0]
