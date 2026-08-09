"""Tests for the stateless formatters in :mod:`contrai_engine.view.formatting`.

Covers the shared labels with real branching: the contract label
(taker seat + double caller, compact vs verbose, optional suit glyph),
the trump label (glyph/label rendering), the seat letter/color lookups
(closed-set ``Position`` keys, no string fallback), the compact card
label used across the trick diamond and hand row, and the bid label
driving the bidding history and diamond.
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Position, Rank, Suit, TeamSide, TrumpVariant
from contrai_core.bid import (
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    SlamLevel,
)
from contrai_core.contract import Contract
from contrai_engine.view.formatting import (
    RANK_SHORT,
    _bid_label,
    _format_card_compact,
    _format_contract_short,
    _format_trump_label,
    _position_color,
    _position_short,
    _suit_glyph,
    _team_abbr,
)
from contrai_engine.view.theme import BLUE, FG, ORANGE, RED


class _UnseatedPlayer:
    """A player never assigned a seat — what ``_seat_letter`` bails on."""

    position = None


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

    def test_a_playerless_contract_falls_back_to_the_team_label(
        self, four_players
    ):
        """Defensive: with no seat to name, the declaring team stands in."""

        north, *_ = four_players
        contract = Contract(ContractBid(north, 100, Suit.HEARTS))
        contract.player = None

        text = _format_contract_short(contract).plain
        assert f"100 by {_team_abbr(TeamSide.NS)}" in text

    @pytest.mark.parametrize(
        ("caller_attr", "marker"),
        [("double_player", "×2"), ("redouble_player", "×4")],
        ids=["double", "redouble"],
    )
    def test_an_unseated_multiplier_caller_omits_the_by_clause(
        self, four_players, caller_attr, marker
    ):
        """A caller with no seat still shows its marker — just unnamed.

        ``Contract.double``/``redouble`` are derived from their player
        attributes, so a marker without *any* caller is impossible. What
        is reachable is a caller whose ``position`` was never set, which
        is what ``_seat_letter`` returns ``None`` for.
        """

        north, east, *_ = four_players
        contract = Contract(
            ContractBid(north, 100, Suit.HEARTS),
            double_player=east,
            redouble_player=north if caller_attr == "redouble_player" else None,
        )
        setattr(contract, caller_attr, _UnseatedPlayer())

        text = _format_contract_short(contract).plain
        assert marker in text
        # "by N" for the taker remains; the multiplier gains no second one.
        assert text.count(" by ") == 1


class TestFormatTrumpLabel:
    """`_format_trump_label` glyph/label rendering."""

    def test_suit_label_has_no_star(self):
        text = _format_trump_label(Suit.HEARTS).plain
        assert "♥ Hearts" in text
        assert "★" not in text

    def test_no_trump_label(self):
        text = _format_trump_label(TrumpVariant.NO_TRUMP).plain
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


class TestFormatCardCompact:
    """`_format_card_compact` — the ``"K♠"`` label used across the screens."""

    @pytest.mark.parametrize("rank", list(Rank))
    def test_uses_the_short_rank_label(self, rank):
        card = Card(Suit.SPADES, rank)
        plain = _format_card_compact(card).plain

        assert plain == f"{RANK_SHORT.get(rank, rank.value)}{_suit_glyph(Suit.SPADES)}"

    def test_ten_is_the_only_two_cell_rank(self):
        """``10`` is why the diamond's card slots are width-3, not width-2."""

        widths = {
            rank: len(RANK_SHORT.get(rank, rank.value)) for rank in Rank
        }
        assert widths[Rank.TEN] == 2
        assert [rank for rank, width in widths.items() if width == 2] == [Rank.TEN]

    @pytest.mark.parametrize("suit", list(Suit))
    def test_appends_the_suit_glyph(self, suit):
        text = _format_card_compact(Card(suit, Rank.KING))
        assert text.plain.endswith(_suit_glyph(suit))

    @pytest.mark.parametrize("suit", [Suit.HEARTS, Suit.DIAMONDS])
    def test_red_suits_are_styled_red(self, suit):
        text = _format_card_compact(Card(suit, Rank.ACE))
        glyph_style = str(text.spans[-1].style)
        assert RED in glyph_style

    @pytest.mark.parametrize("suit", [Suit.SPADES, Suit.CLUBS])
    def test_black_suits_keep_the_plain_foreground(self, suit):
        text = _format_card_compact(Card(suit, Rank.ACE))
        glyph_style = str(text.spans[-1].style)
        assert FG in glyph_style
        assert RED not in glyph_style

    def test_the_rank_is_bold(self):
        text = _format_card_compact(Card(Suit.CLUBS, Rank.JACK))
        assert "bold" in str(text.spans[0].style)


class TestBidLabel:
    """`_bid_label` — the compact glyphs the auction panels render."""

    def test_pass_reads_pass(self, four_players):
        north, *_ = four_players
        assert _bid_label(PassBid(north)).plain == "Pass"

    def test_double_reads_times_two(self, four_players):
        north, *_ = four_players
        assert _bid_label(DoubleBid(north)).plain == "×2"

    def test_redouble_reads_times_four(self, four_players):
        north, *_ = four_players
        assert _bid_label(RedoubleBid(north)).plain == "×4"

    @pytest.mark.parametrize("value", [80, 110, 180])
    def test_numeric_contract_reads_value_then_glyph(self, four_players, value):
        north, *_ = four_players
        bid = ContractBid(north, value, Suit.HEARTS)

        assert _bid_label(bid).plain == f"{value} {_suit_glyph(Suit.HEARTS)}"

    @pytest.mark.parametrize(
        ("level", "label"),
        [(SlamLevel.SLAM, "Slam"), (SlamLevel.SOLO_SLAM, "Solo Slam")],
    )
    def test_slam_family_stringifies_through_slam_level(
        self, four_players, level, label
    ):
        """The label comes from ``SlamLevel.__str__``, not a UI-side map."""

        north, *_ = four_players
        bid = ContractBid(north, level, Suit.SPADES)

        assert _bid_label(bid).plain == f"{label} {_suit_glyph(Suit.SPADES)}"

    def test_no_trump_contract_uses_the_no_trump_glyph(self, four_players):
        north, *_ = four_players
        bid = ContractBid(north, 100, TrumpVariant.NO_TRUMP)

        assert _bid_label(bid).plain.endswith(_suit_glyph(TrumpVariant.NO_TRUMP))

    def test_an_unknown_bid_subclass_falls_back_to_str(self):
        """Defensive: a bid type the view has never heard of still renders."""

        class _MysteryBid:
            def __str__(self) -> str:
                return "mystery"

        assert _bid_label(_MysteryBid()).plain == "mystery"
