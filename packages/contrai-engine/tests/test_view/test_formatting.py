"""Tests for the stateless formatters in :mod:`contrai_engine.view.formatting`.

Covers the shared labels with real branching: the contract label
(taker seat + double caller, compact vs verbose, optional suit glyph),
the trump label (glyph/label rendering), and the seat letter/color
lookups (closed-set ``Position`` keys, no string fallback).
"""

from __future__ import annotations

import pytest

from contrai_core import Position, Suit
from contrai_core.bid import ContractBid, SlamLevel
from contrai_core.contract import Contract
from contrai_engine.view.formatting import (
    _format_contract_short,
    _format_trump_label,
    _position_color,
    _position_short,
)
from contrai_engine.view.theme import BLUE, ORANGE


class TestFormatContractShort:
    """The shared contract label: value + taker seat + double caller.

    Used by the in-game round panel, the after-round recap, and the
    event-log 'Contract set' line — all three render through this.
    """

    def test_plain_contract_names_taker_seat(self, four_players):
        _north, east, *_ = four_players
        contract = Contract(ContractBid(east, 100, Suit.HEARTS))
        text = _format_contract_short(contract).plain
        assert "100 by E" in text
        # No multiplier marker on an un-doubled contract.
        assert "×2" not in text and "×4" not in text

    def test_doubled_contract_names_double_caller(self, four_players):
        north, east, _south, west = four_players
        contract = Contract(
            ContractBid(north, 110, Suit.SPADES),
            double_player=east,
        )
        text = _format_contract_short(contract).plain
        assert "110 by N" in text
        assert "×2 by E" in text

    def test_redoubled_contract_names_redouble_caller(self, four_players):
        north, east, _south, west = four_players
        contract = Contract(
            ContractBid(north, 120, Suit.CLUBS),
            double_player=east,
            redouble_player=north,
        )
        text = _format_contract_short(contract).plain
        assert "120 by N" in text
        # Redouble takes precedence over the double marker.
        assert "×4 by N" in text
        assert "×2" not in text

    def test_suit_glyph_opt_in(self, four_players):
        """suit_glyph=True slots the trump glyph between value and taker."""
        north, *_ = four_players
        contract = Contract(ContractBid(north, 110, Suit.SPADES))
        assert "110 ♠ by N" in _format_contract_short(
            contract, suit_glyph=True
        ).plain
        # Default stays glyph-free: the round panel and recap already
        # carry the trump on their own line.
        assert "♠" not in _format_contract_short(contract).plain

    def test_slam_value_label(self, four_players):
        _north, east, *_ = four_players
        contract = Contract(ContractBid(east, SlamLevel.SLAM, Suit.HEARTS))
        text = _format_contract_short(contract).plain
        assert "Slam by E" in text

    def test_verbose_spells_out_doubled(self, four_players):
        """verbose=True replaces the ×2 glyph with the word 'doubled'."""
        north, east, *_ = four_players
        contract = Contract(
            ContractBid(north, 110, Suit.SPADES),
            double_player=east,
        )
        text = _format_contract_short(contract, verbose=True).plain
        assert "doubled by E" in text
        assert "×2" not in text

    def test_verbose_spells_out_redoubled(self, four_players):
        """verbose=True replaces the ×4 glyph with the word 'redoubled'."""
        north, east, _south, _west = four_players
        contract = Contract(
            ContractBid(north, 120, Suit.CLUBS),
            double_player=east,
            redouble_player=north,
        )
        text = _format_contract_short(contract, verbose=True).plain
        assert "redoubled by N" in text
        assert "×4" not in text
        # Redouble takes precedence: only one marker, not two.
        assert text.count("doubled") == 1


class TestFormatTrumpLabel:
    """`_format_trump_label` glyph/label rendering."""

    def test_suit_label_has_no_star(self):
        text = _format_trump_label(Suit.HEARTS).plain
        assert "♥ Hearts" in text
        assert "★" not in text

    def test_no_trump_label(self):
        text = _format_trump_label(Suit.NO_TRUMP).plain
        assert "No Trump" in text
        assert "★" not in text

    def test_none_suit_is_em_dash(self):
        assert _format_trump_label(None).plain == "—"


class TestPositionShort:
    """`_position_short` — a closed-set lookup, no string-slice fallback."""

    @pytest.mark.parametrize(
        "position, letter",
        [
            (Position.NORTH, "N"),
            (Position.EAST, "E"),
            (Position.SOUTH, "S"),
            (Position.WEST, "W"),
        ],
    )
    def test_maps_each_position_to_its_letter(self, position, letter):
        assert _position_short(position) == letter

    def test_key_outside_the_closed_set_raises_key_error(self):
        """``Position`` is a closed set of four members — a caller that
        slips in anything else (e.g. a raw seat string) hits a
        ``KeyError`` instead of silently degrading to a sliced guess."""
        with pytest.raises(KeyError):
            _position_short("North")


class TestPositionColor:
    """`_position_color` — team color by seat, membership test on `Position`."""

    @pytest.mark.parametrize("position", [Position.NORTH, Position.SOUTH])
    def test_north_south_are_blue(self, position):
        assert _position_color(position) == BLUE

    @pytest.mark.parametrize("position", [Position.EAST, Position.WEST])
    def test_east_west_are_orange(self, position):
        assert _position_color(position) == ORANGE
