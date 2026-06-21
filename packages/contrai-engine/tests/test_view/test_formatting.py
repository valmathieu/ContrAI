"""Tests for the stateless formatters in :mod:`contrai_engine.view.formatting`.

Covers the shared labels with real branching: the contract label
(taker seat + Coinche caller, compact vs verbose) and the trump label
(glyph/label plus the optional ★ flourish).
"""

from __future__ import annotations

from contrai_core import Suit
from contrai_core.bid import ContractBid, SlamLevel
from contrai_core.contract import Contract
from contrai_engine.view.formatting import (
    _format_contract_short,
    _format_trump_label,
)


class TestFormatContractShort:
    """The shared contract label: value + taker seat + Coinche caller.

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

    def test_doubled_contract_names_coincheur(self, four_players):
        north, east, _south, west = four_players
        contract = Contract(
            ContractBid(north, 110, Suit.SPADES),
            double_player=east,
        )
        text = _format_contract_short(contract).plain
        assert "110 by N" in text
        assert "×2 by E" in text

    def test_redoubled_contract_names_surcoincheur(self, four_players):
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
    """`_format_trump_label` glyph/label plus the optional ★ flourish."""

    def test_default_includes_star(self):
        text = _format_trump_label(Suit.HEARTS).plain
        assert "♥ Hearts" in text
        assert "★" in text

    def test_star_false_omits_star(self):
        text = _format_trump_label(Suit.HEARTS, star=False).plain
        assert "♥ Hearts" in text
        assert "★" not in text

    def test_no_trump_label(self):
        text = _format_trump_label(Suit.NO_TRUMP, star=False).plain
        assert "No Trump" in text
        assert "★" not in text

    def test_none_suit_is_em_dash(self):
        assert _format_trump_label(None).plain == "—"
