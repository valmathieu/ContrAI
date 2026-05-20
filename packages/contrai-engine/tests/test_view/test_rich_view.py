"""Tests for the pure helpers in :mod:`contrai_engine.view.rich_view`.

We cover the four functions that have real branching logic and would
break silently if the engine APIs around them shifted:

- ``_parse_bid_input``         — humans type bids, this is the parser.
- ``_parse_card_input``        — humans type card numbers, this validates them.
- ``_sort_hand_for_display``   — trump-first ordering for the hand row.
- ``_current_winner``          — live trick-winner highlight.
- ``_explain_constraint``      — the green "↑ playable …" hint line.

Rendering helpers (``Panel``/``Table`` builders) are not unit-tested — the
smoke-test pass on ``uv run contrai`` validates them end-to-end.
"""

from __future__ import annotations

import pytest

from contrai_core import Card, Rank, Suit, Trick
from contrai_engine.model.player import AiPlayer
from contrai_core.team import Team
from contrai_engine.view.rich_view import (
    RichView,
    _current_winner,
    _explain_constraint,
    _parse_bid_input,
    _parse_card_input,
    _redouble_available_to,
    _sort_hand_for_display,
)


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def four_players():
    """A North/East/South/West quartet wired into N-S and E-W teams."""
    north = AiPlayer("North", "North")
    east = AiPlayer("East", "East")
    south = AiPlayer("South", "South")
    west = AiPlayer("West", "West")
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    north.team = south.team = ns
    east.team = west.team = ew
    return north, east, south, west


# ======================================================================
# _parse_bid_input
# ======================================================================


class TestParseBidInput:
    """Bid-string parser. Returns engine-shaped bid or ``None`` on error."""

    @pytest.mark.parametrize("raw", ["pass", "PASS", "Pass", "p", "passe", " pass "])
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
         "surcontrée", "surcontree"],
    )
    def test_rejects_french_aliases(self, raw):
        """The CLI uses the English vocabulary exclusively. The parser
        used to accept the French aliases ``coinche`` / ``surcoinche`` /
        ``contrée`` / ``surcontrée``; those have been retired."""
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
            ("160 sa", 160, Suit.NO_TRUMP),  # French sans-atout
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
            ("capot s", Suit.SPADES),
            ("capot h", Suit.HEARTS),
            ("capot d", Suit.DIAMONDS),
            ("capot c", Suit.CLUBS),
            ("capots", Suit.SPADES),  # glued
            ("CAPOT H", Suit.HEARTS),  # case-insensitive
        ],
    )
    def test_capot(self, raw, suit):
        assert _parse_bid_input(raw) == ("Capot", suit)

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
            "70 h",      # value below the 80-160 floor
            "85 h",      # value not on the 10-step ladder
            "170 h",     # value above 160
            "abc h",     # non-numeric value
            "80 h s",    # too many tokens
        ],
    )
    def test_rejects_garbage(self, raw):
        assert _parse_bid_input(raw) is None


# ======================================================================
# _redouble_available_to + adaptive bid prompt
# ======================================================================


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


class TestBiddingPromptHint:
    """End-to-end test that the prompt text adapts to the bid history."""

    def _prompt(self, history, next_player):
        view = RichView()
        return view._bidding_prompt_text(history, next_player).plain

    def test_default_hint_when_no_double(self, four_players):
        north, _east, _south, _west = four_players
        history = [(north, "Pass")]
        text = self._prompt(history, north)
        assert "double" in text
        assert "redouble" not in text

    def test_redouble_hint_when_contractor_was_doubled(
        self, four_players
    ):
        north, east, _south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
        ]
        text = self._prompt(history, north)
        assert "redouble" in text
        # The default '80 H' example shouldn't appear in the redouble
        # variant since the only meaningful play is pass/redouble.
        assert "80 H" not in text


# ======================================================================
# _panel_round — round number in the title
# ======================================================================


class TestPanelRoundTitle:
    """The Round panel's title shows the active round number."""

    class _StubRound:
        # Minimal stand-in. _panel_round only reads round_number,
        # contract, dealer, tricks during this phase path.
        def __init__(self, round_number):
            self.round_number = round_number
            self.contract = None
            self.dealer = None
            self.tricks = []
            self.team_tricks = {}

    def test_title_contains_round_number(self):
        view = RichView()
        panel = view._panel_round(self._StubRound(7), phase="bidding")
        assert "Round 7" in panel.title.plain

    def test_title_defaults_when_round_is_none(self):
        view = RichView()
        panel = view._panel_round(None, phase="bidding")
        assert panel.title.plain.startswith("Round")
        # No number when there is no round to talk about.
        assert "Round 0" not in panel.title.plain


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


# ======================================================================
# _sort_hand_for_display
# ======================================================================


class TestSortHandForDisplay:
    """Display-order sort: trump-first, then suit-by-suit, rank desc."""

    def test_no_trump_default_order(self):
        cards = [
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.QUEEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
        ]
        result = _sort_hand_for_display(cards, trump_suit=None)
        # Default suit order: S, H, D, C
        assert [c.suit for c in result] == [
            Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS,
        ]

    def test_trump_goes_first(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.KING),
            Card(Suit.CLUBS, Rank.NINE),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        assert result[0].suit == Suit.HEARTS
        # Non-trump suits keep S, D, C order with hearts removed.
        assert [c.suit for c in result[1:]] == [
            Suit.SPADES, Suit.DIAMONDS, Suit.CLUBS,
        ]

    def test_within_suit_rank_desc_no_trump(self):
        """Within a non-trump suit, highest rank first (normal order)."""
        cards = [
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        assert [c.rank for c in result] == [Rank.ACE, Rank.JACK, Rank.SEVEN]

    def test_within_trump_suit_uses_trump_order(self):
        """Inside the trump suit, the Jack out-ranks the Ace (trump order)."""
        cards = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.SEVEN),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        # Trump order: 7, 8, Q, K, 10, A, 9, J — so J on top, then 9, then A.
        assert [c.rank for c in result] == [
            Rank.JACK, Rank.NINE, Rank.ACE, Rank.SEVEN,
        ]

    def test_empty_suit_skipped(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.KING),
        ]
        result = _sort_hand_for_display(cards, trump_suit=None)
        assert len(result) == 2
        assert {c.suit for c in result} == {Suit.SPADES, Suit.DIAMONDS}

    def test_empty_hand_returns_empty(self):
        assert _sort_hand_for_display([], trump_suit=None) == []
        assert _sort_hand_for_display([], trump_suit=Suit.SPADES) == []


# ======================================================================
# _current_winner
# ======================================================================


class TestCurrentWinner:
    """Live trick-winner computation for the diamond gold-pill highlight."""

    def test_empty_plays_returns_none(self):
        assert _current_winner([], trump_suit=Suit.HEARTS) is None
        assert _current_winner([], trump_suit=None) is None

    def test_single_play_wins(self, four_players):
        north, _, _, _ = four_players
        plays = [(north, Card(Suit.SPADES, Rank.SEVEN))]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is north

    def test_highest_of_led_suit_wins_no_trump_played(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),
            (north, Card(Suit.SPADES, Rank.TEN)),
            (east, Card(Suit.SPADES, Rank.ACE)),  # ace wins
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is east

    def test_off_suit_non_trump_cannot_win(self, four_players):
        """Discarding off-suit (no trump) doesn't take the trick."""
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.SEVEN)),
            (north, Card(Suit.DIAMONDS, Rank.ACE)),  # off suit, no trump
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is west

    def test_trump_beats_non_trump(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.ACE)),
            (north, Card(Suit.HEARTS, Rank.SEVEN)),  # weakest trump still wins
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is north

    def test_highest_trump_wins(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),     # led
            (north, Card(Suit.HEARTS, Rank.NINE)),    # trump
            (east, Card(Suit.HEARTS, Rank.JACK)),     # jack is top trump
            (south, Card(Suit.HEARTS, Rank.ACE)),     # ace below jack/9
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is east

    def test_no_trump_contract_uses_led_suit(self, four_players):
        """``trump_suit=None`` (or NoTrump) means highest led-suit card wins."""
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),
            (north, Card(Suit.SPADES, Rank.ACE)),
            (east, Card(Suit.HEARTS, Rank.JACK)),     # off suit, can't win
        ]
        assert _current_winner(plays, trump_suit=None) is north


# ======================================================================
# _explain_constraint
# ======================================================================


class TestExplainConstraint:
    """Human-readable hint under the hand row."""

    def _make_trick(self, *plays):
        t = Trick()
        for player, card in plays:
            t.add_play(player, card)
        return t

    def test_empty_trick_is_your_lead(self, four_players):
        _, _, south, _ = four_players
        south.hand.clear()
        south.hand.append(Card(Suit.SPADES, Rank.ACE))
        empty = Trick()
        result = _explain_constraint(south, empty, list(south.hand), Suit.HEARTS)
        assert "your lead" in result.plain.lower()

    def test_must_follow_led_suit(self, four_players):
        north, _, south, west = four_players
        # West led ♠K, South has ♠s in hand → must follow.
        south.hand.clear()
        south.hand.extend([
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
        ])
        trick = self._make_trick((west, Card(Suit.SPADES, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.SPADES)
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "must follow" in result.plain
        assert "♠" in result.plain

    def test_must_trump_when_partner_not_winning(self, four_players):
        north, east, south, west = four_players
        # West led ♣K, South has no clubs, has hearts (trump) → must trump.
        south.hand.clear()
        south.hand.extend([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.QUEEN),
        ])
        trick = self._make_trick((west, Card(Suit.CLUBS, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.HEARTS)  # only trumps legal
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "must trump" in result.plain
        # The leader's position label should appear in the hint.
        assert "W" in result.plain

    def test_free_discard_when_no_led_suit_no_trump_obligation(self, four_players):
        """No led-suit in hand, playable includes non-trump → free discard."""
        north, _, south, west = four_players
        south.hand.clear()
        south.hand.extend([
            Card(Suit.DIAMONDS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.TEN),
        ])
        trick = self._make_trick((west, Card(Suit.CLUBS, Rank.KING)))
        # Playable list includes non-trump (Round logic decides — when partner
        # leads, the engine returns the full hand). Here we simulate "free".
        playable = list(south.hand)
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "free discard" in result.plain
