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
from contrai_core.bid import ContractBid, DoubleBid, PassBid, RedoubleBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.team import Team
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.player import AiPlayer, HumanPlayer
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
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == _ids(hand)

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
        legal = round_._get_playable_cards(players["S"])
        assert _ids(legal) == {(Suit.SPADES, Rank.JACK)}

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
        legal = round_._get_playable_cards(players["S"])
        # Must trump even though we can't over-trump.
        assert _ids(legal) == {(Suit.SPADES, Rank.SEVEN)}

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
        legal = round_._get_playable_cards(players["W"])
        assert _ids(legal) == {(Suit.SPADES, Rank.NINE)}

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


# ---------------------------------------------------------------------------
# Belote / rebelote tracking
# ---------------------------------------------------------------------------


class TestBeloteHolderDetection:
    """``_detect_belote_holder`` finds the player holding K+Q of trump."""

    def test_sets_belote_holder_when_pair_present(self, players):
        contract = _contract(players["N"], 100, Suit.HEARTS)
        round_ = _make_round(
            players,
            {
                "N": [],
                "E": [],
                "S": [
                    Card(Suit.HEARTS, Rank.KING),
                    Card(Suit.HEARTS, Rank.QUEEN),
                ],
                "W": [],
            },
            contract,
            [],
        )
        round_._detect_belote_holder()
        assert round_.belote_holder is players["S"]

    def test_no_holder_when_pair_split(self, players):
        contract = _contract(players["N"], 100, Suit.HEARTS)
        round_ = _make_round(
            players,
            {
                "N": [Card(Suit.HEARTS, Rank.KING)],
                "E": [],
                "S": [Card(Suit.HEARTS, Rank.QUEEN)],
                "W": [],
            },
            contract,
            [],
        )
        round_._detect_belote_holder()
        assert round_.belote_holder is None

    def test_no_holder_at_no_trump(self, players):
        contract = _contract(players["N"], 100, Suit.NO_TRUMP)
        round_ = _make_round(
            players,
            {
                "N": [],
                "E": [],
                "S": [
                    Card(Suit.HEARTS, Rank.KING),
                    Card(Suit.HEARTS, Rank.QUEEN),
                    Card(Suit.SPADES, Rank.KING),
                    Card(Suit.SPADES, Rank.QUEEN),
                ],
                "W": [],
            },
            contract,
            [],
        )
        round_._detect_belote_holder()
        assert round_.belote_holder is None


class TestBeloteTransition:
    """State machine for belote → rebelote announcements."""

    def _setup(self, players):
        contract = _contract(players["N"], 100, Suit.HEARTS)
        # South holds both K♥ and Q♥ plus filler.
        round_ = _make_round(
            players,
            {
                "N": [],
                "E": [],
                "S": [
                    Card(Suit.HEARTS, Rank.KING),
                    Card(Suit.HEARTS, Rank.QUEEN),
                    Card(Suit.SPADES, Rank.SEVEN),
                ],
                "W": [],
            },
            contract,
            [],
        )
        round_.belote_holder = players["S"]
        return round_

    def test_first_play_returns_belote(self, players):
        round_ = self._setup(players)
        card = Card(Suit.HEARTS, Rank.KING)
        assert round_._is_belote_event(players["S"], card) is True
        kind = round_._transition_belote_state(players["S"])
        assert kind == "belote"
        assert round_.belote_state == {players["S"]: "belote"}

    def test_second_play_returns_rebelote(self, players):
        round_ = self._setup(players)
        round_._transition_belote_state(players["S"])  # first → belote
        kind = round_._transition_belote_state(players["S"])
        assert kind == "rebelote"
        assert round_.belote_state == {players["S"]: "rebelote"}

    def test_non_kq_trump_not_an_event(self, players):
        round_ = self._setup(players)
        # Seven of trump is not part of the pair.
        assert (
            round_._is_belote_event(
                players["S"], Card(Suit.HEARTS, Rank.SEVEN)
            )
            is False
        )

    def test_non_holder_not_an_event(self, players):
        round_ = self._setup(players)
        # N plays K♥ — but N is not the belote holder.
        assert (
            round_._is_belote_event(players["N"], Card(Suit.HEARTS, Rank.KING))
            is False
        )


# ---------------------------------------------------------------------------
# Auto-pass when partner has doubled/redoubled
# ---------------------------------------------------------------------------


def _empty_round(players_dict):
    """A Round with no contract / no trick — enough for bidding helpers."""
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    return Round(order, dealer=players_dict["N"], deck=None, round_number=1)


class TestShouldAutoPass:
    """Round._should_auto_pass: the *only meaningful action is pass* check."""

    def test_empty_history_no_auto_pass(self, players):
        round_ = _empty_round(players)
        assert round_._should_auto_pass(players["S"], []) is False

    def test_only_passes_no_auto_pass(self, players):
        round_ = _empty_round(players)
        bids = [PassBid(players["N"]), PassBid(players["E"])]
        assert round_._should_auto_pass(players["S"], bids) is False

    def test_partner_doubled_triggers_auto_pass(self, players):
        """E (opponent) bids 100 ♥; N (S's partner) doubles. S's only
        meaningful next action is Pass."""
        round_ = _empty_round(players)
        bids = [
            ContractBid(players["E"], 100, Suit.HEARTS),
            DoubleBid(players["N"]),
        ]
        assert round_._should_auto_pass(players["S"], bids) is True

    def test_opponent_doubled_no_auto_pass(self, players):
        """N (S's partner) bids 100 ♥; E (opponent) doubles. S is on
        the contracting team and CAN redouble — must not auto-pass."""
        round_ = _empty_round(players)
        bids = [
            ContractBid(players["N"], 100, Suit.HEARTS),
            DoubleBid(players["E"]),
        ]
        assert round_._should_auto_pass(players["S"], bids) is False

    def test_partner_redoubled_triggers_auto_pass(self, players):
        """N bids 100, W doubles, S (N's partner) redoubles. Now N is
        up — partner just redoubled and there's nothing left to do."""
        round_ = _empty_round(players)
        bids = [
            ContractBid(players["N"], 100, Suit.HEARTS),
            DoubleBid(players["W"]),
            RedoubleBid(players["S"]),
        ]
        assert round_._should_auto_pass(players["N"], bids) is True

    def test_passes_after_double_still_trigger_auto_pass(self, players):
        """A pass between the partner-double and our turn doesn't break
        the rule — what matters is the last NON-PASS bid."""
        round_ = _empty_round(players)
        bids = [
            ContractBid(players["E"], 100, Suit.HEARTS),
            DoubleBid(players["N"]),
            PassBid(players["W"]),
        ]
        assert round_._should_auto_pass(players["S"], bids) is True


class TestManageBiddingAutoPasses:
    """End-to-end: the manage_bidding loop never asks the view when
    the player should be auto-passed."""

    def test_human_is_not_prompted_after_partner_double(self, players):
        """Stub view that records request_bid_action calls. Pre-script
        a bidding sequence that lands the human (S) right after their
        partner (N) doubled the opponents' bid.

        Sequence (cyclic order W → N → E → S):
          1. W: 100 ♥
          2. N (S's partner): Double          ← DoubleBid is valid only
                                                immediately after the
                                                ContractBid, so the
                                                doubler MUST be next in
                                                cycle after the contractor.
          3. E (W's partner, contracting team): pass
          4. S (HUMAN): AUTO-PASS — partner doubled
          5. W: pass    (now passes_count = 3 → bidding ends)
        """
        # Make S a HumanPlayer so the view path is exercised.
        human = HumanPlayer("You", "South")
        human.team = players["S"].team  # same N-S team
        players["S"] = human

        # Pre-seed each AI's choose_bid via a scripted queue.
        scripted = {
            players["W"]: [(100, Suit.HEARTS), "Pass", "Pass", "Pass"],
            players["N"]: ["Double", "Pass", "Pass", "Pass"],
            players["E"]: ["Pass", "Pass", "Pass", "Pass"],
        }
        for ai, choices in scripted.items():
            queue = list(choices)
            ai.choose_bid = lambda *_args, _q=queue: (
                _q.pop(0) if _q else "Pass"
            )

        # Stub view: records request_bid_action calls. Asserting it
        # is NEVER called is the whole point of the test.
        prompts = []

        class _View:
            def request_bid_action(self, player, history):
                prompts.append((player, list(history)))
                return "Pass"

        round_ = _empty_round(players)
        # Cycle order: W → N → E → S (dealer is S, so the next player
        # after the dealer leads).
        round_.players_order = [
            players["W"], players["N"], players["E"], players["S"],
        ]

        contract = round_.manage_bidding(view=_View())

        # W contracted 100 ♥; N (S's partner) doubled.
        assert contract is not None
        assert contract.value == 100
        assert contract.suit == Suit.HEARTS
        assert contract.double is True
        # And the critical assertion: S was never prompted.
        assert prompts == []
