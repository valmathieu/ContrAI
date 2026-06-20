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

from contrai_core import Auction, Hand
from contrai_core.bid import ContractBid, DoubleBid, PassBid, RedoubleBid, SlamLevel
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.exceptions import IllegalPlayError, PlayRuleViolation
from contrai_core.team import Team
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.player import AiPlayer, HumanPlayer, wire_to_bid
from contrai_engine.model.round import Round, UnannouncedSlam


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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["S"])
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
        legal = round_._get_playable_cards(players["W"])
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
        legal = round_._get_playable_cards(players["S"])
        assert set(legal) == {Card(Suit.HEARTS, Rank.ACE)}


# ---------------------------------------------------------------------------
# Illegal-play classifier — _classify_play_violation
# ---------------------------------------------------------------------------
#
# These mirror the legality scenarios above, but feed the classifier an
# *illegal* in-hand card and assert the PlayRuleViolation it returns. The
# classifier's branch order must stay in sync with _get_playable_cards.


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
            round_._classify_play_violation(players["S"], illegal)
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
            round_._classify_play_violation(players["S"], illegal)
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
            round_._classify_play_violation(players["S"], illegal)
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
            round_._classify_play_violation(players["W"], illegal)
            == PlayRuleViolation.MUST_OVERTRUMP
        )


class TestPlayTrickRejectsIllegalCard:
    """play_trick raises IllegalPlayError instead of silently correcting
    an illegal card returned by choose_card."""

    def test_illegal_card_raises_illegal_play_error(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        n_card = Card(Suit.HEARTS, Rank.KING)
        e_follow = Card(Suit.HEARTS, Rank.ACE)
        e_illegal = Card(Suit.SPADES, Rank.JACK)  # trump, but E holds a heart
        round_ = _make_round(
            players,
            {"N": [n_card], "E": [e_illegal, e_follow], "S": [], "W": []},
            contract,
            [],  # play_trick starts a fresh trick itself
        )
        # Scripted choices: N leads its only heart, E tries the illegal trump.
        players["N"].choose_card = (
            lambda trick, c, playable, _card=n_card: _card
        )
        players["E"].choose_card = (
            lambda trick, c, playable, _card=e_illegal: _card
        )

        with pytest.raises(IllegalPlayError) as excinfo:
            round_.play_trick()

        assert excinfo.value.card is e_illegal
        assert excinfo.value.reason == PlayRuleViolation.MUST_FOLLOW_SUIT
        assert set(excinfo.value.legal_cards) == {Card(Suit.HEARTS, Rank.ACE)}


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
# Auto-pass when partner has doubled / redoubled (end-to-end)
# ---------------------------------------------------------------------------
#
# The unit-level "only Pass is legal" cases moved to
# ``packages/contrai-core/tests/test_auction.py`` (see
# ``TestLegalActions``) when the auction logic moved to
# :class:`contrai_core.Auction`. The remaining test here is the
# integration story: even when an auto-pass case applies for the human
# seat, Round must never call ``view.request_bid_action`` — that is the
# UX promise the player sees as "I am not asked to confirm Pass".


def _empty_round(players_dict):
    """A Round with no contract / no trick — enough for bidding helpers."""
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    return Round(order, dealer=players_dict["N"], deck=None, round_number=1)


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

        # Pre-seed each AI's choose_bid via a scripted queue. Lambdas
        # consume wire-format entries and lift them through
        # ``wire_to_bid`` so the returned objects match the new
        # :class:`Bid`-typed signature of ``Player.choose_bid``.
        scripted = {
            players["W"]: [(100, Suit.HEARTS), "Pass", "Pass", "Pass"],
            players["N"]: ["Double", "Pass", "Pass", "Pass"],
            players["E"]: ["Pass", "Pass", "Pass", "Pass"],
        }
        for ai, choices in scripted.items():
            queue = list(choices)
            ai.choose_bid = lambda _auction, _p=ai, _q=queue: wire_to_bid(
                _p, _q.pop(0) if _q else "Pass"
            )

        # Stub view: records request_bid_action calls. Asserting it
        # is NEVER called is the whole point of the test.
        prompts = []

        class _View:
            def request_bid_action(self, player, auction):
                prompts.append((player, list(auction.bids)))
                return PassBid(player)

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


# ---------------------------------------------------------------------------
# Slam / Solo Slam scoring (calculate_round_scores)
# ---------------------------------------------------------------------------
#
# Tests below build a Round directly and stuff it with the minimal state
# the scoring path reads:
#   - ``self.contract``         — drives base / multiplier / family check.
#   - ``self.team_tricks``      — number of tricks per team (length used).
#   - ``self.tricks``           — per-trick winners (used by Solo Slam).
#   - ``self.last_trick_winner``— "dix de der" (irrelevant for Slam family).
#
# Cards inside each Trick only matter when belote / card points are
# computed; for Slam family they are not — we still seed at least one
# card per trick so :meth:`Trick.get_current_winner` has something to
# answer with.


def _slam_round(
    players_dict,
    *,
    contract,
    trick_winners,
):
    """Build a Round with synthesised tricks.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        contract: a Contract bound to one of the players.
        trick_winners: ordered list of seat letters — one per completed
            trick. Each entry is the player who wins that trick. Cards
            are filler (the suit-7), and the winner leads it so
            :meth:`Trick.get_current_winner` returns them.

    Returns:
        Round with ``contract``, ``tricks``, ``team_tricks``, and
        ``last_trick_winner`` populated.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    round_ = Round(order, dealer=players_dict["N"], deck=None, round_number=1)
    round_.contract = contract

    # Filler card per trick: a low non-trump card. The winner plays it
    # solo so get_current_winner returns them regardless of trump.
    filler = Card(Suit.CLUBS, Rank.SEVEN)
    for seat in trick_winners:
        trick = Trick()
        trick.add_play(players_dict[seat], filler)
        round_.tricks.append(trick)
        winner = players_dict[seat]
        if winner.team is not None:
            round_.team_tricks[winner.team.name].append(trick)

    if trick_winners:
        round_.last_trick_winner = players_dict[trick_winners[-1]]
    return round_


class TestSlamScoring:
    """Symmetric grid: 500 / 1000 / 2000 to the winning side."""

    def test_slam_made_normal_attacker_scores_500(self, players):
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 500
        assert scores["East-West"] == 0

    def test_slam_failed_normal_defender_scores_500(self, players):
        # Attacker (N) takes only 7 tricks; W steals one → contract fails.
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 500

    def test_slam_made_doubled_attacker_scores_1000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 1000
        assert scores["East-West"] == 0

    def test_slam_failed_doubled_defender_scores_1000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        winners = ["N"] * 6 + ["E", "W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_slam_made_redoubled_attacker_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 2000
        assert scores["East-West"] == 0

    def test_slam_failed_redoubled_defender_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 2000

    def test_slam_team_partner_wins_a_trick_still_makes(self, players):
        """Plain Slam only cares about the TEAM winning all 8. The
        partner taking some tricks is fine — that's the Solo Slam
        rule, not Slam."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        # N takes 5, partner S takes 3 → team owns all 8 → contract made.
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 500
        assert scores["East-West"] == 0


class TestSoloSlamScoring:
    """Bidder-personally rule + 1000 / 2000 / 4000 symmetric grid."""

    def test_solo_slam_made_bidder_takes_all_8(self, players):
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 1000
        assert scores["East-West"] == 0

    def test_solo_slam_failed_when_partner_takes_a_trick(self, players):
        """Key Solo Slam invariant: team owning all 8 tricks is NOT
        enough — the bidder personally must win them all."""
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["S"]  # partner wins the last trick
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        # Team took all 8 tricks, but partner won one → Solo Slam fails.
        # Defenders score the at-risk amount.
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_solo_slam_failed_when_opponent_takes_a_trick(self, players):
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_solo_slam_made_doubled_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 2000
        assert scores["East-West"] == 0

    def test_solo_slam_made_redoubled_scores_4000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 4000
        assert scores["East-West"] == 0

    def test_solo_slam_failed_redoubled_defender_scores_4000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        winners = ["N"] * 7 + ["S"]  # partner steals one → Solo Slam fails
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 4000


class TestSlamFamilyBeloteLayering:
    """Belote (+20) applies on top of the Slam grid for whichever team
    *holds* the K + Q of trump, independent of who wins the contract."""

    def test_slam_made_belote_to_attacker(self, players):
        """Slam made, attacker holds belote → 500 + 20 to attacker."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        round_.belote_holder = players["N"]  # N-S holds K+Q of trump
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 520  # 500 + 20
        assert scores["East-West"] == 0

    def test_slam_failed_belote_to_defender(self, players):
        """Slam failed, defender holds belote → 500 + 20 to defender."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_holder = players["W"]  # E-W holds K+Q of trump
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 520  # 500 + 20

    def test_slam_failed_belote_to_attacker_independent_of_contract(
        self, players
    ):
        """Belote is independent of contract outcome: attacker can hold
        belote even when they lost the contract → defender scores 500,
        attacker still scores +20."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_holder = players["N"]  # attacker holds belote
        scores = round_.calculate_round_scores()
        # Attacker still gets +20 from belote even though the contract failed.
        assert scores["North-South"] == 20
        assert scores["East-West"] == 500


class TestNumericContractScoringRegression:
    """Confirms numeric (80–180) contracts are *not* affected by the
    Slam-family branch added during this refactor."""

    @staticmethod
    def _trick_with_card(seat_player, card):
        trick = Trick()
        trick.add_play(seat_player, card)
        return trick

    def test_numeric_made_normal_uses_base_plus_card_points(self, players):
        """80 made by N-S without double, and *not* a capot: attacker =
        80 + card points, defender = its own card points. Trump = clubs;
        the bidder plays the trump Jack (20 pts) in seven tricks while
        E-W steal one 0-point trick — so the plain made formula, not the
        unannounced-capot substitute, is the path under test."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["N"], deck=None, round_number=1
        )
        round_.contract = contract
        # Seven tricks where N plays the trump Jack solo — 20 pts each.
        # (Card identity is fine — Card doesn't have unique-per-instance
        # invariants we care about for scoring.)
        for _ in range(7):
            trick = self._trick_with_card(
                players["N"], Card(Suit.CLUBS, Rank.JACK)
            )
            round_.tricks.append(trick)
            round_.team_tricks["North-South"].append(trick)
        # E-W steal a single 0-point trick so N-S did not sweep all 8.
        ew_trick = self._trick_with_card(
            players["E"], Card(Suit.HEARTS, Rank.SEVEN)
        )
        round_.tricks.append(ew_trick)
        round_.team_tricks["East-West"].append(ew_trick)
        round_.last_trick_winner = players["N"]
        scores = round_.calculate_round_scores()
        # Card points = 20*7 = 140; dix de der = +10 → 150 card pts.
        # Contract made (150 >= 80) → attacker score = 80 + 150 = 230.
        assert round_.unannounced_capot is None
        assert scores["North-South"] == 230
        # E-W captured a single 0-point trick → 0 card points.
        assert scores["East-West"] == 0

    def test_numeric_failed_normal_defender_gets_160_plus_base(self, players):
        """Failed 80 contract by N-S: defender gets (160 + 80) * 1 = 240."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        # 0 tricks to N — contract fails immediately on points (0 < 80).
        round_ = _slam_round(
            players, contract=contract, trick_winners=["E"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 240


# ---------------------------------------------------------------------------
# Numeric scoring — belote attribution & doubled (winner-takes-all)
# ---------------------------------------------------------------------------
#
# These build a Round directly and stuff ``team_tricks`` with synthesised
# tricks. Scoring only sums ``card.get_points(trump)`` over each team's
# tricks, so the trick *shape* (how many cards, who else played) is
# irrelevant — we can pack all of a team's point-carrying cards into a
# single Trick. Trump = hearts throughout, where the trump-aware values
# are J=20, 9=14, A=11, 10=10, K=4, Q=3, 8=7=0.


def _numeric_round(
    players_dict,
    *,
    contract,
    team_cards,
    last_trick_winner=None,
    belote_holder=None,
):
    """Build a numeric-contract Round with synthesised tricks.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        contract: a numeric Contract bound to one of the players.
        team_cards: mapping team-name → list of ``(seat, Card)`` plays.
            Each team's cards are packed into Tricks of up to four cards
            (the Trick capacity), all credited to that team.
        last_trick_winner: seat letter credited with the dix de der, or
            None.
        belote_holder: seat letter holding K + Q of trump, or None.

    Returns:
        Round with ``contract``, ``tricks``, ``team_tricks``,
        ``last_trick_winner`` and ``belote_holder`` populated.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    round_ = Round(order, dealer=players_dict["N"], deck=None, round_number=1)
    round_.contract = contract
    for team_name, plays in team_cards.items():
        # Trick holds at most four cards — chunk the team's plays so the
        # synthesised pile spans as many tricks as needed.
        for start in range(0, len(plays), 4):
            trick = Trick()
            for seat, card in plays[start:start + 4]:
                trick.add_play(players_dict[seat], card)
            round_.tricks.append(trick)
            round_.team_tricks[team_name].append(trick)
    if last_trick_winner is not None:
        round_.last_trick_winner = players_dict[last_trick_winner]
    if belote_holder is not None:
        round_.belote_holder = players_dict[belote_holder]
    return round_


class TestNumericBeloteByHolder:
    """Belote follows the *holder* of K + Q of trump, never the team that
    merely captures those cards in a trick. This is the Problem-1
    regression: a phantom capture-based +20 used to flip a failed
    contract into a spurious "made"."""

    # All eight hearts = 62 trump-aware points, including both K and Q.
    _HEART_RANKS = (
        Rank.JACK, Rank.NINE, Rank.ACE, Rank.TEN,
        Rank.KING, Rank.QUEEN, Rank.EIGHT, Rank.SEVEN,
    )

    def _all_hearts_for(self, seat):
        return [(seat, Card(Suit.HEARTS, r)) for r in self._HEART_RANKS]

    def test_captured_kq_without_holder_does_not_make_contract(self, players):
        """E-W capture all hearts (incl. K+Q, 62 pts) but no single
        player *holds* the pair → no belote. Bare 62 < 80 → the contract
        FAILS. Under the old capture-based rule the phantom +20 would
        have lifted 62→82 and "made" the 80 contract — the bug behind
        the impossible recap."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "East-West": self._all_hearts_for("E"),
                "North-South": [],
            },
            last_trick_winner="N",  # der to N-S, not the declarer
            belote_holder=None,     # pair is split — nobody holds it
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is False
        assert scores["East-West"] == 0
        assert scores["North-South"] == 240  # 160 + 80

    def test_belote_credited_to_holder_even_if_opponent_captures(self, players):
        """E-W capture the K+Q in their tricks, but S (N-S) *held* the
        pair → the +20 belote is credited to N-S, the holder, not E-W."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "East-West": self._all_hearts_for("E"),
                "North-South": [],
            },
            last_trick_winner="N",
            belote_holder="S",  # N-S holds the pair
        )
        scores = round_.calculate_round_scores()
        # Declarer E-W realized 62 < 80 → failed → 0.
        assert scores["East-West"] == 0
        # Defender N-S: 160 + 80 (winner-takes-all, M=1) + 20 belote.
        assert scores["North-South"] == 260

    def test_failed_declarer_keeps_only_its_belote(self, players):
        """A failed declarer keeps its belote bonus (always preserved)
        and nothing else."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "East-West": [
                    ("E", Card(Suit.HEARTS, Rank.KING)),
                    ("E", Card(Suit.HEARTS, Rank.QUEEN)),
                ],
                "North-South": [],
            },
            last_trick_winner="N",
            belote_holder="E",  # declarer holds the pair
        )
        scores = round_.calculate_round_scores()
        # E-W realized = 7 cards + 20 belote = 27 < 80 → failed.
        assert round_.contract_made is False
        assert scores["East-West"] == 20    # belote only
        assert scores["North-South"] == 240  # 160 + 80


class TestNumericDoubledScoring:
    """Doubled / redoubled numeric contracts: winner-takes-all, the loser
    scores 0 except its belote. The winner amount is 160 + C×M whether it
    is the made declarer or the winning defense."""

    @staticmethod
    def _ns_big_pile():
        """76 trump-aware points for N-S — clears an 80 contract once the
        dix de der is added."""
        return [
            ("N", Card(Suit.HEARTS, Rank.JACK)),  # 20
            ("N", Card(Suit.HEARTS, Rank.NINE)),  # 14
            ("N", Card(Suit.HEARTS, Rank.ACE)),   # 11
            ("N", Card(Suit.HEARTS, Rank.TEN)),   # 10
            ("S", Card(Suit.SPADES, Rank.ACE)),   # 11
            ("S", Card(Suit.SPADES, Rank.TEN)),   # 10
        ]

    def test_doubled_made_defender_scores_zero(self, players):
        """Doubled contract made: the defending side scores 0 even though
        it captured point-carrying cards (Problem 2)."""
        contract = Contract(
            ContractBid(players["N"], 80, Suit.HEARTS),
            double_player=players["E"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "North-South": self._ns_big_pile(),
                # E-W win a fat trick — under the old rule they'd keep
                # these 14 points; winner-takes-all zeroes them.
                "East-West": [
                    ("E", Card(Suit.DIAMONDS, Rank.TEN)),  # 10
                    ("E", Card(Suit.CLUBS, Rank.KING)),    # 4
                ],
            },
            last_trick_winner="N",  # +10 der → N-S realized 86 ≥ 80
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is True
        assert scores["North-South"] == 320  # 160 + 80*2
        assert scores["East-West"] == 0

    def test_doubled_made_defender_keeps_only_belote(self, players):
        """The lone exception: the losing defender keeps its belote."""
        contract = Contract(
            ContractBid(players["N"], 80, Suit.HEARTS),
            double_player=players["E"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "North-South": self._ns_big_pile(),
                "East-West": [("E", Card(Suit.CLUBS, Rank.KING))],
            },
            last_trick_winner="N",
            belote_holder="E",  # E-W (defender) holds the pair
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 320  # 160 + 80*2
        assert scores["East-West"] == 20     # belote only

    def test_doubled_failed_winner_takes_160_plus_cm(self, players):
        """Doubled contract failed: the defense takes 160 + C×M, declarer 0."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.HEARTS),
            double_player=players["E"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "North-South": [("N", Card(Suit.DIAMONDS, Rank.TEN))],  # 10 < 100
                "East-West": [("E", Card(Suit.HEARTS, Rank.JACK))],
            },
            last_trick_winner="E",
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is False
        assert scores["North-South"] == 0
        assert scores["East-West"] == 360  # 160 + 100*2

    def test_redoubled_failed_winner_takes_160_plus_c_times_four(self, players):
        """Redoubled failed: the defense takes 160 + C×4 — the same shape
        as a made redoubled declarer (symmetric stake)."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.HEARTS),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "North-South": [("N", Card(Suit.DIAMONDS, Rank.TEN))],
                "East-West": [("E", Card(Suit.HEARTS, Rank.JACK))],
            },
            last_trick_winner="E",
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 560  # 160 + 100*4


# ---------------------------------------------------------------------------
# Unannounced capot scoring (calculate_round_scores)
# ---------------------------------------------------------------------------
#
# When the declaring team wins all 8 tricks on an *un-doubled* numeric
# contract without having bid a Slam, the 162-point pile (152 cards + 10
# dix de der) is replaced by a flat 250 substitute: the declarer scores
# contract value + 250 (+ belote), the defence scores nothing, and the
# contract is necessarily made. The round is flagged UnannouncedSlam.GRAND_SLAM
# when the contracting player personally won all 8 tricks, else
# UnannouncedSlam.SLAM. A doubled/redoubled sweep keeps the winner-takes-all
# 160 + C×M shape, and a defence sweep is unaffected (declaring team only).


class TestUnannouncedSlamEnum:
    """The UnannouncedSlam member value is its display label."""

    def test_member_labels_via_str(self):
        assert str(UnannouncedSlam.SLAM) == "Slam"
        assert str(UnannouncedSlam.GRAND_SLAM) == "Grand Slam"


class TestUnannouncedSlamScoring:
    """Un-doubled numeric sweep by the declaring team → contract + 250."""

    def test_team_sweep_scores_contract_plus_250(self, players):
        """N takes 5, partner S takes 3 → the *team* swept (but no single
        player did) → UnannouncedSlam.SLAM, scored 100 + 250."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is UnannouncedSlam.SLAM
        assert round_.contract_made is True
        assert scores["North-South"] == 350  # 100 + 250
        assert scores["East-West"] == 0

    def test_bidder_personal_sweep_is_grand_slam(self, players):
        """N wins all 8 personally → UnannouncedSlam.GRAND_SLAM (same 250 substitute)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is UnannouncedSlam.GRAND_SLAM
        assert scores["North-South"] == 350  # 100 + 250
        assert scores["East-West"] == 0

    def test_capot_forces_made_below_threshold(self, players):
        """The filler tricks carry 0 card points, so a 180 contract could
        never clear its threshold on cards — but sweeping every trick
        makes it outright → 180 + 250 = 430."""
        contract = _contract(players["N"], 180, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is True
        assert scores["North-South"] == 430  # 180 + 250
        assert scores["East-West"] == 0

    def test_capot_layers_belote_on_top(self, players):
        """Belote (+20) still credits the holder on top of contract + 250."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_holder = players["N"]  # N-S holds K+Q of trump
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 370  # 100 + 250 + 20
        assert scores["East-West"] == 0

    def test_doubled_sweep_keeps_winner_takes_all_and_is_unflagged(self, players):
        """A doubled contract swept by the declarer keeps the
        winner-takes-all 160 + C×M shape — no 250 substitute, no flag."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.SPADES),
            double_player=players["E"],
        )
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=None, round_number=1)
        round_.contract = contract
        # N sweeps all 8 with the trump Jack (20 pts each → 160 card
        # points, clearing the 100 threshold). Card identity is
        # irrelevant to scoring, so the same Card may recur.
        for _ in range(8):
            trick = Trick()
            trick.add_play(players["N"], Card(Suit.SPADES, Rank.JACK))
            round_.tricks.append(trick)
            round_.team_tricks["North-South"].append(trick)
        round_.last_trick_winner = players["N"]
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is None
        assert round_.contract_made is True
        assert scores["North-South"] == 360  # 160 + 100*2
        assert scores["East-West"] == 0

    def test_defense_sweep_is_not_a_capot(self, players):
        """Declaring team only: when the *defence* sweeps, the declarer
        simply fails (160 + C to the defence) — no 250, not flagged."""
        contract = _contract(players["E"], 100, Suit.SPADES)  # E-W declares
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is None
        assert round_.contract_made is False
        assert scores["East-West"] == 0
        assert scores["North-South"] == 260  # 160 + 100 (normal failed)
