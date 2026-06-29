"""Tests for the card-legality oracle in
``contrai_engine.model.round.legality`` — ``get_playable_cards`` (the
legal-play set) and ``classify_play_violation`` (why an in-hand card is
illegal).

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

These build a minimal ``Round`` with a hand-picked trick state and call
the module functions directly, passing ``round_.contract`` and
``round_.current_trick`` — exercising the oracle's branches in isolation
from the full ``manage_bidding`` + ``play_all_tricks`` path. The shared
``players`` fixture lives in ``conftest.py``.
"""

from __future__ import annotations

from contrai_core import Hand
from contrai_core.bid import ContractBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.exceptions import PlayRuleViolation
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.round import Round
from contrai_engine.model.round.legality import (
    classify_play_violation,
    get_playable_cards,
)


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


# ---------------------------------------------------------------------------
# Over-trump rule when trump is led
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
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == {Card(Suit.SPADES, Rank.JACK)}

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
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == {
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.SPADES, Rank.SEVEN),
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
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == {
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
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
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == set(hand)


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
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == {
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
        }

    def test_partner_master_free_discard(self, players):
        """N (partner) led ♥A. E followed ♥7. Partner is still master.
        S has no hearts, no trump obligation → free discard."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.SEVEN),
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.HEARTS, Rank.SEVEN))],
        )
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == set(hand)

    def test_partner_overtrumped_must_trump(self, players):
        """N (partner) led ♥A. E (opponent) over-trumped with ♠7.
        Partner is no longer master → S must trump (and over-trump
        the ♠7 with anything higher, here ♠J)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.SEVEN),
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.SEVEN))],
        )
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == {Card(Suit.SPADES, Rank.JACK)}

    def test_partner_led_then_partner_overtaken_must_trump(self, players):
        """Symmetric scenario where S has no hearts AND no trump
        higher than the opponent's overtrump — must still play a
        trump (even a lower one). The non-trump cards are now off-limits."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.SPADES, Rank.SEVEN),  # below opponent's ♠ J
            Card(Suit.DIAMONDS, Rank.ACE),
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.JACK))],
        )
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        # Must trump even though we can't over-trump.
        assert set(legal) == {Card(Suit.SPADES, Rank.SEVEN)}

    def test_three_card_partial_opponent_master_forces_overtrump(self, players):
        """Three-card partial trick: N♥A, E♠7, S♠A. S is now master
        (S♠A beats E's ♠7 in trump order). It is W's turn. W's partner
        is E (not master) — the master is the opponent S → W must
        over-trump S♠A. In trump order ♠A is rank 5, only ♠9 (rank 6)
        and ♠J (rank 7) beat it. W has ♠9 (legal) and ♠8 (illegal)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand_w = [
            Card(Suit.SPADES, Rank.NINE),    # beats ♠A
            Card(Suit.SPADES, Rank.EIGHT),   # below ♠A in trump order
            Card(Suit.DIAMONDS, Rank.SEVEN),
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": [], "W": hand_w},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.SEVEN)),
             ("S", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = get_playable_cards(players["W"], round_.contract, round_.current_trick)
        assert set(legal) == {Card(Suit.SPADES, Rank.NINE)}

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
        legal = get_playable_cards(players["S"], round_.contract, round_.current_trick)
        assert set(legal) == {Card(Suit.HEARTS, Rank.ACE)}


# ---------------------------------------------------------------------------
# Illegal-play classifier — classify_play_violation
# ---------------------------------------------------------------------------
#
# These mirror the legality scenarios above, but feed the classifier an
# *illegal* in-hand card and assert the PlayRuleViolation it returns. The
# classifier's branch order must stay in sync with get_playable_cards.


class TestClassifyPlayViolation:
    def test_off_suit_while_holding_lead_is_follow_violation(self, players):
        """N leads ♥K. S holds hearts but tries the ♠J (trump) → must
        follow suit."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.SPADES, Rank.JACK)
        hand = [
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
            illegal,  # trump but lead is hearts
        ]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.HEARTS, Rank.KING))],
        )
        assert (
            classify_play_violation(
                players["S"], illegal, round_.contract, round_.current_trick
            )
            == PlayRuleViolation.MUST_FOLLOW_SUIT
        )

    def test_too_low_trump_when_trump_led_is_overtrump_violation(self, players):
        """N leads ♠7 (trump), E plays ♠A. S holds ♠J (master) and ♠8;
        playing the ♠8 → must over-trump."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.SPADES, Rank.EIGHT)
        hand = [Card(Suit.SPADES, Rank.JACK), illegal]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        assert (
            classify_play_violation(
                players["S"], illegal, round_.contract, round_.current_trick
            )
            == PlayRuleViolation.MUST_OVERTRUMP
        )

    def test_discard_while_void_and_holding_trump_is_trump_violation(self, players):
        """E (opponent) leads ♥A — no trump on the table yet. S is void in
        hearts, holds ♠J (trump) but discards ♦A → must trump."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.DIAMONDS, Rank.ACE)
        hand = [Card(Suit.SPADES, Rank.JACK), illegal]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": hand, "W": []},
            contract,
            [("E", Card(Suit.HEARTS, Rank.ACE))],
        )
        assert (
            classify_play_violation(
                players["S"], illegal, round_.contract, round_.current_trick
            )
            == PlayRuleViolation.MUST_TRUMP
        )

    def test_under_trump_over_opponent_ruff_is_overtrump_violation(self, players):
        """Three-card partial: N♥A, E♠7, S♠A. W (opponent of master S) is
        void in hearts, holds ♠9 (beats ♠A) and ♠8 (below it); playing the
        ♠8 → must over-trump."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.SPADES, Rank.EIGHT)
        hand_w = [Card(Suit.SPADES, Rank.NINE), illegal, Card(Suit.DIAMONDS, Rank.SEVEN)]
        round_ = _make_round(
            players,
            {"N": [], "E": [], "S": [], "W": hand_w},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.SEVEN)),
             ("S", Card(Suit.SPADES, Rank.ACE))],
        )
        assert (
            classify_play_violation(
                players["W"], illegal, round_.contract, round_.current_trick
            )
            == PlayRuleViolation.MUST_OVERTRUMP
        )
