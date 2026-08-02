"""Unit tests for the Rich-free debug projections in ``debug_state``.

These functions are the reuse surface a future non-Rich interface will
consume, so the tests pin their *data* contracts: plain containers,
plain strings, no markup.
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Position, Rank, Suit
from contrai_core.team import Team
from contrai_engine.debug_state import (
    cards_still_in_play,
    deal_lines,
    hand_snapshot,
    round_result_lines,
    sort_cards_trump_first,
)
from contrai_engine.model.player import AiPlayer


@pytest.fixture
def four_players():
    """A North/East/South/West quartet wired into N-S and E-W teams."""
    north = AiPlayer("North", Position.NORTH)
    east = AiPlayer("East", Position.EAST)
    south = AiPlayer("South", Position.SOUTH)
    west = AiPlayer("West", Position.WEST)
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    north.team = south.team = ns
    east.team = west.team = ew
    return north, east, south, west


def _give(player, *cards):
    """Replace ``player``'s hand contents with ``cards``."""
    player.hand.clear()
    player.hand.extend(cards)


class TestSortCardsTrumpFirst:
    def test_trump_block_leads_in_trump_order(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.CLUBS, Rank.SEVEN),
        ]
        result = sort_cards_trump_first(cards, Suit.HEARTS)
        # Hearts (trump) first — J above 9 on the trump scale — then
        # the plain suits in preference order.
        assert result == [
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.CLUBS, Rank.SEVEN),
        ]

    def test_no_trump_keeps_suit_preference_order(self):
        cards = [
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.SPADES, Rank.KING),
        ]
        result = sort_cards_trump_first(cards, None)
        # Spades before clubs; 10 outranks K on the plain scale.
        assert result == [
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.CLUBS, Rank.ACE),
        ]

    def test_input_list_is_not_mutated(self):
        cards = [Card(Suit.CLUBS, Rank.ACE), Card(Suit.SPADES, Rank.TEN)]
        snapshot = list(cards)
        sort_cards_trump_first(cards, Suit.CLUBS)
        assert cards == snapshot


class TestCardsStillInPlay:
    def test_groups_by_suit_high_to_low(self, four_players):
        north, east, south, west = four_players
        _give(north, Card(Suit.SPADES, Rank.KING), Card(Suit.HEARTS, Rank.ACE))
        _give(east, Card(Suit.SPADES, Rank.ACE))
        _give(south, Card(Suit.SPADES, Rank.TEN))
        _give(west)
        grouped = cards_still_in_play(four_players)
        # Plain-scale order inside each suit: A > 10 > K.
        assert grouped[Suit.SPADES] == [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.SPADES, Rank.KING),
        ]
        assert grouped[Suit.HEARTS] == [Card(Suit.HEARTS, Rank.ACE)]

    def test_every_suit_present_even_when_exhausted(self, four_players):
        for player in four_players:
            _give(player)
        grouped = cards_still_in_play(four_players)
        assert set(grouped) == set(Suit)
        assert all(cards == [] for cards in grouped.values())


class TestHandSnapshot:
    def test_returns_trump_first_copy(self, four_players):
        north, *_ = four_players
        _give(
            north,
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.NINE),
        )
        snapshot = hand_snapshot(north, Suit.DIAMONDS)
        assert snapshot == [
            Card(Suit.DIAMONDS, Rank.NINE),
            Card(Suit.SPADES, Rank.ACE),
        ]
        # A copy — mutating it leaves the hand alone.
        snapshot.clear()
        assert len(north.hand) == 2

    def test_without_trump_uses_plain_suit_preference(self, four_players):
        """Before a contract exists there is no trump block to lead."""
        north, *_ = four_players
        _give(
            north,
            Card(Suit.CLUBS, Rank.ACE),
            Card(Suit.SPADES, Rank.TEN),
        )
        assert hand_snapshot(north, None) == [
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.CLUBS, Rank.ACE),
        ]


class _StubRound:
    """Just the attributes ``deal_lines`` / ``round_result_lines`` read."""

    def __init__(self, players_order, dealer, round_number=1):
        self.players_order = players_order
        self.dealer = dealer
        self.round_number = round_number
        self.contract = None
        self.contract_made = None
        self.round_scores = {}


class _StubContract:
    def __init__(self, value, suit, player, team):
        self.value = value
        self.suit = suit
        self.player = player
        self.team = team


class TestDealLines:
    def test_header_plus_one_line_per_seat(self, four_players):
        north, east, south, west = four_players
        for player in four_players:
            _give(player, Card(Suit.SPADES, Rank.ACE))
        round_ = _StubRound([east, south, west, north], dealer=north,
                            round_number=3)
        lines = deal_lines(round_)
        assert len(lines) == 5
        assert all(isinstance(line, str) for line in lines)
        assert lines[0] == "Round #3 dealt — dealer N"
        # All four seats appear, in canonical seating order (N first).
        assert [line[0] for line in lines[1:]] == ["N", "W", "S", "E"]

    def test_absent_dealer_renders_a_dash(self, four_players):
        """A round with no dealer set still yields a full header line."""
        north, east, south, west = four_players
        for player in four_players:
            _give(player)
        round_ = _StubRound([north, east, south, west], dealer=None)
        assert deal_lines(round_)[0] == "Round #1 dealt — dealer —"

    def test_cards_render_as_plain_glyph_labels(self, four_players):
        north, east, south, west = four_players
        _give(north, Card(Suit.SPADES, Rank.ACE), Card(Suit.SPADES, Rank.TEN))
        for player in (east, south, west):
            _give(player)
        round_ = _StubRound([north, east, south, west], dealer=west)
        lines = deal_lines(round_)
        north_line = next(l for l in lines if l.startswith("N:"))
        assert north_line == "N: A♠ 10♠"


class TestRoundResultLines:
    def _contracted_round(self, four_players, made):
        north, east, south, west = four_players
        round_ = _StubRound([north, east, south, west], dealer=west,
                            round_number=2)
        round_.contract = _StubContract(100, Suit.HEARTS, east, east.team)
        round_.contract_made = made
        round_.round_scores = {"North-South": 0, "East-West": 162}
        return round_

    def test_made_branch(self, four_players):
        round_ = self._contracted_round(four_players, made=True)
        lines = round_result_lines(
            round_, {"North-South": 40, "East-West": 300}
        )
        assert lines == [
            "Round #2: contract 100 ♥ by E — made.",
            "Round points: North-South 0 · East-West 162",
            "Totals: North-South 40 · East-West 300",
        ]

    def test_failed_branch(self, four_players):
        round_ = self._contracted_round(four_players, made=False)
        lines = round_result_lines(round_, {"North-South": 0, "East-West": 0})
        assert "— failed." in lines[0]

    def test_failed_branch_does_not_rederive_from_a_nonzero_score(
        self, four_players
    ):
        """A failed declarer can still score a non-zero Belote bonus —
        ``round_scores`` alone must never flip the verdict back to made.
        ``contract_made`` is the only signal read for the outcome word.
        """
        round_ = self._contracted_round(four_players, made=False)
        # The declaring side (East-West) still nets a non-zero round
        # score (its Belote bonus) despite failing the contract.
        round_.round_scores = {"North-South": 260, "East-West": 20}
        lines = round_result_lines(
            round_, {"North-South": 260, "East-West": 20}
        )
        assert "— failed." in lines[0]
        assert "made" not in lines[0]

    def test_all_pass_branch_skips_round_points(self, four_players):
        north, east, south, west = four_players
        round_ = _StubRound([north, east, south, west], dealer=west,
                            round_number=4)
        lines = round_result_lines(round_, {"North-South": 10, "East-West": 20})
        assert lines == [
            "Round #4: all passed — redeal.",
            "Totals: North-South 10 · East-West 20",
        ]
