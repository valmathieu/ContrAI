"""Tests for the card-legality oracle exposed by :class:`PlayState`.

``PlayState.legal_actions`` answers "what may this player play?" and
``PlayState.apply`` surfaces "why was that card illegal?" through the
:class:`IllegalPlayError` it raises. The rules under test come from the
contrée follow / trump obligations:

    1. Follow suit if possible.
    2. When trump is led, over-trump if you hold a higher trump than the
       highest already played; otherwise any trump.
    3. When you cannot follow suit and your partner is *not* currently
       master of the trick, you must trump (and over-trump opponents if
       able).
    4. Partner-master exemption: if your partner is currently winning the
       trick, you may discard freely.
    5. Otherwise discard.

Each test seeds a :class:`PlayState` directly through the bare constructor
with a hand-picked trick state, then queries ``legal_actions`` (for the
legal set) or drives ``apply`` (for the violation classification). The
shared ``players`` fixture lives in ``conftest.py``.
"""

from __future__ import annotations

import pytest

from contrai_core import (
    BasePlayer,
    Card,
    Contract,
    IllegalPlayError,
    PlayRuleViolation,
    PlayState,
    Rank,
    Suit,
)
from contrai_core.bid import ContractBid
from contrai_core.play import Play


def _make_state(
    players_dict: dict[str, BasePlayer],
    hands: dict[str, list[Card]],
    contract: Contract | None,
    plays: list[tuple[str, Card]],
    order: tuple[str, ...] = ("N", "E", "S", "W"),
) -> PlayState:
    """Build a :class:`PlayState` wired to the supplied trick state.

    Args:
        players_dict: mapping of seat letter to :class:`BasePlayer` (from
            the ``players`` fixture).
        hands: mapping of seat letter to the remaining cards in that
            player's hand.
        contract: a :class:`Contract` (provides trump) or ``None``.
        plays: ordered ``(seat_letter, Card)`` pairs already played in the
            current trick.
        order: the seating rotation as seat letters; ``order[0]`` leads
            trick 0. Defaults to N/E/S/W.

    Returns:
        A :class:`PlayState` whose ``plays`` and per-seat ``hands`` reflect
        the arguments — built via the unvalidated bare constructor so
        arbitrary mid-trick states can be injected.
    """
    seating = tuple(players_dict[s] for s in order)
    hand_tuples = tuple(tuple(hands.get(s, [])) for s in order)
    play_tuples = tuple(Play(players_dict[s], card) for s, card in plays)
    return PlayState(
        contract=contract,
        players=seating,
        hands=hand_tuples,
        plays=play_tuples,
    )


def _contract(player: BasePlayer, value: int, suit: Suit) -> Contract:
    """Build a :class:`Contract` at ``value`` in ``suit`` for ``player``."""
    return Contract(ContractBid(player, value, suit))


# ---------------------------------------------------------------------------
# Over-trump rule when trump is led
# ---------------------------------------------------------------------------


class TestOverTrumpWhenTrumpIsLed:
    """Must beat the highest trump on the table when trump is led."""

    def test_higher_trump_available_forces_overtrump(self, players):
        """N leads ♠ 7 (trump), E plays ♠ A (current best trump, order 5).
        S holds ♠ J (master, order 7) and ♠ 8 (order 1).
        S must play the ♠ J — the ♠ 8 is illegal."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [Card(Suit.SPADES, Rank.JACK), Card(Suit.SPADES, Rank.EIGHT)]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == {Card(Suit.SPADES, Rank.JACK)}

    def test_only_lower_trumps_falls_back_to_all_trumps(self, players):
        """E plays the ♠ J (the absolute master). S holds only weaker
        trumps — every one is legal."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [Card(Suit.SPADES, Rank.EIGHT), Card(Suit.SPADES, Rank.SEVEN)]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.JACK))],
        )
        # Lead is ♠7, but the follow-suit rule already filters to ♠ — the
        # over-trump branch then sees no higher trump and returns the full
        # follow-suit set.
        legal = state.legal_actions(players["S"])
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
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == {
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
        }

    def test_no_trump_at_all_allows_free_discard(self, players):
        """Trump led and S has none → can discard anything (the trump suit
        doesn't compete with the led suit for the off-suit hand)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [Card(Suit.HEARTS, Rank.ACE), Card(Suit.DIAMONDS, Rank.KING)]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == set(hand)


# ---------------------------------------------------------------------------
# Sanity scenarios for non-trump-led tricks
# ---------------------------------------------------------------------------


class TestFollowSuitWhenNonTrumpLed:
    def test_must_follow_lead_suit(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),  # trump but lead is hearts
        ]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.HEARTS, Rank.KING))],
        )
        legal = state.legal_actions(players["S"])
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
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.HEARTS, Rank.SEVEN))],
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == set(hand)

    def test_partner_overtrumped_must_trump(self, players):
        """N (partner) led ♥A. E (opponent) over-trumped with ♠7.
        Partner is no longer master → S must trump (and over-trump the ♠7
        with anything higher, here ♠J)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.SEVEN),
        ]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.SEVEN))],
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == {Card(Suit.SPADES, Rank.JACK)}

    def test_partner_led_then_partner_overtaken_must_trump(self, players):
        """S has no hearts AND no trump higher than the opponent's
        overtrump — must still play a trump (even a lower one). The
        non-trump cards are now off-limits."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand = [
            Card(Suit.SPADES, Rank.SEVEN),  # below opponent's ♠ J
            Card(Suit.DIAMONDS, Rank.ACE),
        ]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.JACK))],
        )
        legal = state.legal_actions(players["S"])
        # Must trump even though we can't over-trump.
        assert set(legal) == {Card(Suit.SPADES, Rank.SEVEN)}

    def test_three_card_partial_opponent_master_forces_overtrump(self, players):
        """Three-card partial trick: N♥A, E♠7, S♠A. S is now master
        (S♠A beats E's ♠7 in trump order). It is W's turn. W's partner is
        E (not master) — the master is the opponent S → W must over-trump
        S♠A. In trump order ♠A is rank 5, only ♠9 (rank 6) and ♠J (rank 7)
        beat it. W has ♠9 (legal) and ♠8 (illegal)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        hand_w = [
            Card(Suit.SPADES, Rank.NINE),    # beats ♠A
            Card(Suit.SPADES, Rank.EIGHT),   # below ♠A in trump order
            Card(Suit.DIAMONDS, Rank.SEVEN),
        ]
        state = _make_state(
            players,
            {"W": hand_w},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.SEVEN)),
             ("S", Card(Suit.SPADES, Rank.ACE))],
        )
        legal = state.legal_actions(players["W"])
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
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("E", Card(Suit.HEARTS, Rank.KING)),
             ("N", Card(Suit.HEARTS, Rank.SEVEN))],
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == {Card(Suit.HEARTS, Rank.ACE)}


# ---------------------------------------------------------------------------
# Illegal-play classification via apply()
# ---------------------------------------------------------------------------
#
# These mirror the legality scenarios above, but drive apply() with an
# *illegal* in-hand card and assert the PlayRuleViolation the raised
# IllegalPlayError carries. The seating rotation and preceding plays are
# arranged so the acting player is genuinely ``to_act`` — apply enforces
# turn order, then classifies the follow / trump obligation that was broken.


class TestClassifyPlayViolation:
    def test_off_suit_while_holding_lead_is_follow_violation(self, players):
        """N leads ♥K, E follows ♥8; S is to act. S holds hearts but tries
        the ♠J (trump) → must follow suit."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.SPADES, Rank.JACK)
        hand = [
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
            illegal,  # trump but lead is hearts
        ]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.HEARTS, Rank.KING)),
             ("E", Card(Suit.HEARTS, Rank.EIGHT))],
        )
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(players["S"], illegal))
        assert excinfo.value.reason == PlayRuleViolation.MUST_FOLLOW_SUIT

    def test_too_low_trump_when_trump_led_is_overtrump_violation(self, players):
        """N leads ♠7 (trump), E plays ♠A; S is to act. S holds ♠J (master)
        and ♠8; playing the ♠8 → must over-trump."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.SPADES, Rank.EIGHT)
        hand = [Card(Suit.SPADES, Rank.JACK), illegal]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("N", Card(Suit.SPADES, Rank.SEVEN)),
             ("E", Card(Suit.SPADES, Rank.ACE))],
        )
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(players["S"], illegal))
        assert excinfo.value.reason == PlayRuleViolation.MUST_OVERTRUMP

    def test_discard_while_void_and_holding_trump_is_trump_violation(self, players):
        """E (opponent) leads ♥A — no trump on the table yet; S is to act.
        S is void in hearts, holds ♠J (trump) but discards ♦A → must trump."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.DIAMONDS, Rank.ACE)
        hand = [Card(Suit.SPADES, Rank.JACK), illegal]
        state = _make_state(
            players,
            {"S": hand},
            contract,
            [("E", Card(Suit.HEARTS, Rank.ACE))],
            order=("E", "S", "W", "N"),
        )
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(players["S"], illegal))
        assert excinfo.value.reason == PlayRuleViolation.MUST_TRUMP

    def test_under_trump_over_opponent_ruff_is_overtrump_violation(self, players):
        """Three-card partial: N♥A, E♠7, S♠A. W (opponent of master S) is to
        act, void in hearts, holds ♠9 (beats ♠A) and ♠8 (below it); playing
        the ♠8 → must over-trump."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        illegal = Card(Suit.SPADES, Rank.EIGHT)
        hand_w = [
            Card(Suit.SPADES, Rank.NINE),
            illegal,
            Card(Suit.DIAMONDS, Rank.SEVEN),
        ]
        state = _make_state(
            players,
            {"W": hand_w},
            contract,
            [("N", Card(Suit.HEARTS, Rank.ACE)),
             ("E", Card(Suit.SPADES, Rank.SEVEN)),
             ("S", Card(Suit.SPADES, Rank.ACE))],
        )
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(players["W"], illegal))
        assert excinfo.value.reason == PlayRuleViolation.MUST_OVERTRUMP
