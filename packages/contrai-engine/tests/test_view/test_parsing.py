"""Tests for the human-input parsers in :mod:`contrai_engine.view.parsing`.

Humans type bids and card numbers at the prompt; these two parsers turn
that text into engine-shaped values (or ``None`` so the loops re-ask).
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Rank, Suit
from contrai_core.bid import SlamLevel
from contrai_engine.view.parsing import _parse_bid_input, _parse_card_input


# ======================================================================
# _parse_bid_input
# ======================================================================


class TestParseBidInput:
    """Bid-string parser. Returns engine-shaped bid or ``None`` on error."""

    @pytest.mark.parametrize("raw", ["pass", "PASS", "Pass", "p", " pass "])
    def test_pass_variants(self, raw):
        assert _parse_bid_input(raw) == "Pass"

    @pytest.mark.parametrize(
        "raw", ["double", "d", "Double", "DOUBLE", " double "]
    )
    def test_double_variants(self, raw):
        assert _parse_bid_input(raw) == "Double"

    @pytest.mark.parametrize(
        "raw", ["redouble", "r", "Redouble", "REDOUBLE", " redouble "]
    )
    def test_redouble_variants(self, raw):
        assert _parse_bid_input(raw) == "Redouble"

    @pytest.mark.parametrize(
        "raw",
        ["coinche", "surcoinche", "contrée", "contree",
         "surcontrée", "surcontree", "passe"],
    )
    def test_rejects_french_aliases(self, raw):
        """The CLI uses the English vocabulary exclusively. The parser
        used to accept the French aliases ``coinche`` / ``surcoinche`` /
        ``contrée`` / ``surcontrée`` / ``passe``; those have been
        retired."""
        assert _parse_bid_input(raw) is None

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
            ("160 nt", 160, Suit.NO_TRUMP),
            ("160 notrump", 160, Suit.NO_TRUMP),
            ("80 ♥", 80, Suit.HEARTS),
            ("80 ♠", 80, Suit.SPADES),
        ],
    )
    def test_contract_bid_separated(self, raw, value, suit):
        assert _parse_bid_input(raw) == (value, suit)

    @pytest.mark.parametrize(
        "raw,value,suit",
        [
            ("100h", 100, Suit.HEARTS),
            ("80s", 80, Suit.SPADES),
            ("130c", 130, Suit.CLUBS),
        ],
    )
    def test_contract_bid_glued(self, raw, value, suit):
        """Value and suit may be glued together with no separator."""
        assert _parse_bid_input(raw) == (value, suit)

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
    def test_slam(self, raw, suit):
        assert _parse_bid_input(raw) == (SlamLevel.SLAM, suit)

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
    def test_solo_slam(self, raw, suit):
        assert _parse_bid_input(raw) == (SlamLevel.SOLO_SLAM, suit)

    def test_capital_letters_in_value_suit(self):
        assert _parse_bid_input("100 H") == (100, Suit.HEARTS)

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
            "190 h",     # value above the 180 ceiling
            "abc h",     # non-numeric value
            "80 h s",    # too many tokens
            "capot s",   # legacy name no longer accepted
            "160 sa",    # French sans-atout alias no longer accepted
        ],
    )
    def test_rejects_garbage(self, raw):
        assert _parse_bid_input(raw) is None


# ======================================================================
# _parse_card_input
# ======================================================================


class TestParseCardInput:
    """Card-number parser. Validates that the picked card is playable."""

    @pytest.fixture
    def hand(self):
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
