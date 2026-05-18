"""Tests for ``Round._get_playable_cards`` — the legality oracle.

The rules under test come from ``contree-domain.md`` §6.2-§6.3:

    1. Follow suit if possible.
    2. When trump is led, over-trump if you hold a higher trump than
       the highest already played; otherwise any trump.
    3. When you cannot follow suit and your partner is *not* currently
       master of the trick, you must trump (and over-trump opponents
       if able).
    4. Partner-master exemption: if your partner is currently winning
       the trick, you may discard freely.
    5. Otherwise discard.

These tests build a minimal ``Round`` with a hand-picked trick state
and ask for the legal-play set. They avoid the full ``manage_bidding``
+ ``play_all_tricks`` path so the oracle's branches can be exercised
in isolation.
"""

from __future__ import annotations

import pytest

from contrai_core import Hand
from contrai_core.bid import ContractBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.team import Team
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.player import AiPlayer
from contrai_engine.model.round import Round


# ---------------------------------------------------------------------------
# Fixtures — four positioned players + their teams
# ---------------------------------------------------------------------------


@pytest.fixture
def players():
    north = AiPlayer("N", "North")
    east = AiPlayer("E", "East")
    south = AiPlayer("S", "South")
    west = AiPlayer("W", "West")
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    for p in (north, south):
        p.team = ns
    for p in (east, west):
        p.team = ew
    return {"N": north, "E": east, "S": south, "W": west}


def _make_round(players_dict, hands, contract, plays):
    """Build a ``Round`` wired to the supplied state.

    Args:
        players_dict: mapping of seat letter → Player (from the
            ``players`` fixture).
        hands: mapping of seat letter → list of Cards in that player's
            hand.
        contract: a Contract object (provides trump) or None.
        plays: ordered list of (seat_letter, Card) tuples — the cards
            already played in the current trick.

    Returns:
        A Round whose ``current_trick`` reflects ``plays`` and whose
        ``players_order`` is the four players in N/E/S/W order.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    for seat, cards in hands.items():
        players_dict[seat].hand = Hand(cards)
    round_ = Round(order, dealer=players_dict["N"], deck=None, round_number=1)
    round_.contract = contract
    round_.current_trick = Trick()
    for seat, card in plays:
        round_.current_trick.add_play(players_dict[seat], card)
    return round_


def _contract(player, value, suit):
    return Contract(ContractBid(player, value, suit))


def _ids(cards):
    """Return (suit, rank) tuples for a list of Cards.

    Cards don't override ``__eq__``/``__hash__`` (they compare by
    identity), so we project onto the canonical pair when comparing
    *which* cards are in a legal-play set.
    """
    return {(c.suit, c.rank) for c in cards}


# ---------------------------------------------------------------------------
# Over-trump rule when trump is led (commit 2 target)
# ---------------------------------------------------------------------------


class TestOverTrumpWhenTrumpIsLed:
    """contree-domain.md §6.3 — must beat the highest trump on the table."""

    def test_higher_trump_available_forces_overtrump(self, players):
        """N leads ♠ 7 (trump), E plays ♠ A (current best trump, order 5).
        S holds ♠ J (master, order 7) and ♠ 8 (order 1).
        S must play the ♠ J — the ♠ 8 is illegal."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [Card(Suit.SPADES, Rank.JACK), Card(Suit.SPADES, Rank.EIGHT)]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == {(Suit.SPADES, Rank.JACK)}

    def test_only_lower_trumps_falls_back_to_all_trumps(self, players):
        """E plays the ♠ J (the absolute master). S holds only weaker
        trumps — every one is legal."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [Card(Suit.SPADES, Rank.EIGHT), Card(Suit.SPADES, Rank.SEVEN)]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.JACK))],
        )
        # NOTE: lead is ♠7, but follow-suit rule already filters to ♠ —
        # the over-trump branch then sees no higher trump and returns
        # the full follow-suit set.
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == {
            (Suit.SPADES, Rank.EIGHT),
            (Suit.SPADES, Rank.SEVEN),
        }

    def test_multiple_higher_trumps_returns_all_higher(self, players):
        """Both ♠ J and ♠ 9 beat the ♠ A on the table; both are legal."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.EIGHT),
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == {
            (Suit.SPADES, Rank.JACK),
            (Suit.SPADES, Rank.NINE),
        }

    def test_no_trump_at_all_allows_free_discard(self, players):
        """Trump led and S has none → can discard anything (the trump
        suit doesn't compete with the led suit for the off-suit hand)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [Card(Suit.HEARTS, Rank.ACE), Card(Suit.DIAMONDS, Rank.KING)]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == _ids(hand)


# ---------------------------------------------------------------------------
# Sanity scenarios for non-trump-led tricks (regression coverage so
# the over-trump fix doesn't drift into the wrong branch)
# ---------------------------------------------------------------------------


class TestFollowSuitWhenNonTrumpLed:
    def test_must_follow_lead_suit(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),  # trump but lead is hearts
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.HEARTS, Rank.KING))],
        )
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == {
            (Suit.HEARTS, Rank.SEVEN),
            (Suit.HEARTS, Rank.ACE),
        }

    def test_opponent_led_and_partner_followed_must_follow_suit(self, players):
        """E (opponent) led ♥K; N (partner) played ♥7 in follow. S has
        hearts → must follow suit."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("E", Card(Suit.HEARTS, Rank.KING)),
             ("N", Card(Suit.HEARTS, Rank.SEVEN))],
        )
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == {(Suit.HEARTS, Rank.ACE)}
