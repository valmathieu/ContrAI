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
from contrai_core.bid import ContractBid, DoubleBid, PassBid, RedoubleBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.team import Team
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.player import AiPlayer, HumanPlayer, wire_to_bid
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
        contract = _contract(players["N"], "Slam", Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 500
        assert scores["East-West"] == 0

    def test_slam_failed_normal_defender_scores_500(self, players):
        # Attacker (N) takes only 7 tricks; W steals one → contract fails.
        contract = _contract(players["N"], "Slam", Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 500

    def test_slam_made_doubled_attacker_scores_1000(self, players):
        contract = Contract(
            ContractBid(players["N"], "Slam", Suit.SPADES), double=True
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 1000
        assert scores["East-West"] == 0

    def test_slam_failed_doubled_defender_scores_1000(self, players):
        contract = Contract(
            ContractBid(players["N"], "Slam", Suit.SPADES), double=True
        )
        winners = ["N"] * 6 + ["E", "W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_slam_made_redoubled_attacker_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], "Slam", Suit.SPADES),
            double=True,
            redouble=True,
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 2000
        assert scores["East-West"] == 0

    def test_slam_failed_redoubled_defender_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], "Slam", Suit.SPADES),
            double=True,
            redouble=True,
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
        contract = _contract(players["N"], "Slam", Suit.SPADES)
        # N takes 5, partner S takes 3 → team owns all 8 → contract made.
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 500
        assert scores["East-West"] == 0


class TestSoloSlamScoring:
    """Bidder-personally rule + 1000 / 2000 / 4000 symmetric grid."""

    def test_solo_slam_made_bidder_takes_all_8(self, players):
        contract = _contract(players["N"], "SoloSlam", Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 1000
        assert scores["East-West"] == 0

    def test_solo_slam_failed_when_partner_takes_a_trick(self, players):
        """Key Solo Slam invariant: team owning all 8 tricks is NOT
        enough — the bidder personally must win them all."""
        contract = _contract(players["N"], "SoloSlam", Suit.SPADES)
        winners = ["N"] * 7 + ["S"]  # partner wins the last trick
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        # Team took all 8 tricks, but partner won one → Solo Slam fails.
        # Defenders score the at-risk amount.
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_solo_slam_failed_when_opponent_takes_a_trick(self, players):
        contract = _contract(players["N"], "SoloSlam", Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_solo_slam_made_doubled_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], "SoloSlam", Suit.SPADES), double=True
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 2000
        assert scores["East-West"] == 0

    def test_solo_slam_made_redoubled_scores_4000(self, players):
        contract = Contract(
            ContractBid(players["N"], "SoloSlam", Suit.SPADES),
            double=True,
            redouble=True,
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 4000
        assert scores["East-West"] == 0

    def test_solo_slam_failed_redoubled_defender_scores_4000(self, players):
        contract = Contract(
            ContractBid(players["N"], "SoloSlam", Suit.SPADES),
            double=True,
            redouble=True,
        )
        winners = ["N"] * 7 + ["S"]  # partner steals one → Solo Slam fails
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 4000


class TestSlamFamilyBeloteLayering:
    """Belote (+20) applies on top of the Slam grid for whichever team
    holds it, independent of who wins the contract."""

    @staticmethod
    def _trick_with_kq_of_trump(holder, trump):
        """One trick where ``holder`` plays both K and Q of trump.

        Used so the belote-detection scan in ``calculate_round_scores``
        finds both ranks of trump in the holder team's captured tricks
        and awards the +20 bonus.
        """
        trick = Trick()
        trick.add_play(holder, Card(trump, Rank.KING))
        trick.add_play(holder, Card(trump, Rank.QUEEN))
        return trick

    def test_slam_made_belote_to_attacker(self, players):
        """Slam made, attacker holds belote → 500 + 20 to attacker."""
        contract = _contract(players["N"], "Slam", Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        # Splice K+Q of trump into one of N's captured tricks so the
        # belote-detection scan flips North-South's belote flag.
        kq_trick = self._trick_with_kq_of_trump(players["N"], Suit.SPADES)
        round_.tricks[0] = kq_trick
        round_.team_tricks["North-South"][0] = kq_trick
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 520  # 500 + 20
        assert scores["East-West"] == 0

    def test_slam_failed_belote_to_defender(self, players):
        """Slam failed, defender holds belote → 500 + 20 to defender."""
        contract = _contract(players["N"], "Slam", Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        # Splice K+Q of trump into W's captured trick.
        kq_trick = self._trick_with_kq_of_trump(players["W"], Suit.SPADES)
        round_.tricks[-1] = kq_trick
        round_.team_tricks["East-West"][0] = kq_trick
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 520  # 500 + 20

    def test_slam_failed_belote_to_attacker_independent_of_contract(
        self, players
    ):
        """Belote is independent of contract outcome: attacker can hold
        belote even when they lost the contract → defender scores 500,
        attacker still scores +20."""
        contract = _contract(players["N"], "Slam", Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        # Splice K+Q of trump into N's captured trick (attacker holds belote
        # even though they failed the contract).
        kq_trick = self._trick_with_kq_of_trump(players["N"], Suit.SPADES)
        round_.tricks[0] = kq_trick
        round_.team_tricks["North-South"][0] = kq_trick
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
        """80 made by N-S without double: attacker = 80 + card points,
        defender = card points. Trump = clubs; bidder plays the trump
        Jack alone in every trick (20 pts × 8 = 160), so card points
        clear the 80 threshold and the formula path is exercised."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["N"], deck=None, round_number=1
        )
        round_.contract = contract
        # Eight tricks where N plays the trump Jack solo — 20 pts each.
        # (Card identity is fine — Card doesn't have unique-per-instance
        # invariants we care about for scoring.)
        for _ in range(8):
            trick = self._trick_with_card(
                players["N"], Card(Suit.CLUBS, Rank.JACK)
            )
            round_.tricks.append(trick)
            round_.team_tricks["North-South"].append(trick)
        round_.last_trick_winner = players["N"]
        scores = round_.calculate_round_scores()
        # Card points = 20*8 = 160; dix de der = +10 → 170 card pts.
        # Contract made (170 >= 80) → attacker score = 80 + 170 = 250.
        assert scores["North-South"] == 250
        # E-W captured no tricks → 0 card points.
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
