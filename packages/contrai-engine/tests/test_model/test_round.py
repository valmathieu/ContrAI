"""Tests for the ``Round`` lifecycle orchestrator.

Covers the parts that stay in the orchestrator after the pure scoring
and legality transformations were carved into sibling modules:

    * ``play_trick`` rejecting an illegal card with ``IllegalPlayError``
      (the orchestrator's enforcement seam over ``legality``);
    * belote / rebelote detection and the announcement state machine;
    * the ``manage_bidding`` auto-pass UX promise (the human is never
      prompted when Pass is the only legal action).

The legal-play oracle itself lives in ``test_round_legality.py`` and the
scoring grid in ``test_round_scoring.py``. The shared ``players`` fixture
lives in ``conftest.py``.
"""

from __future__ import annotations

import pytest

from contrai_core import Hand
from contrai_core.bid import ContractBid, PassBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.team import Team
from contrai_core.exceptions import IllegalPlayError, PlayRuleViolation
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.player import AiPlayer, HumanPlayer, wire_to_bid
from contrai_engine.model.round import Round


# ---------------------------------------------------------------------------
# Scenario builders. The shared ``players`` fixture lives in ``conftest.py``.
# ---------------------------------------------------------------------------


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


class TestPlayTrickHumanUsesView:
    """A human's card is sourced from the view, never from
    ``HumanPlayer.choose_card`` (which only returns None by design)."""

    def test_human_card_comes_from_view_not_choose_card(self):
        human = HumanPlayer("H", "North")
        east = AiPlayer("E", "East")
        south = AiPlayer("S", "South")
        west = AiPlayer("W", "West")
        order = [human, east, south, west]
        ns = Team("North-South", [human, south])
        ew = Team("East-West", [east, west])
        for p in (human, south):
            p.team = ns
        for p in (east, west):
            p.team = ew

        contract = _contract(human, 100, Suit.SPADES)
        # One heart each so following suit is trivial; human leads.
        cards = {
            human: Card(Suit.HEARTS, Rank.KING),
            east: Card(Suit.HEARTS, Rank.SEVEN),
            south: Card(Suit.HEARTS, Rank.EIGHT),
            west: Card(Suit.HEARTS, Rank.NINE),
        }
        for player, card in cards.items():
            player.hand = Hand([card])

        class _StubDeck:
            def add_cards(self, cards):
                pass

        round_ = Round(order, dealer=human, deck=_StubDeck(), round_number=1)
        round_.contract = contract

        # Spy: the human's choose_card must NOT be called on the view path.
        human_calls = []
        human.choose_card = (  # type: ignore[method-assign]
            lambda *args, _calls=human_calls: _calls.append(args)
        )
        # Bots play their single legal card straight through choose_card.
        for player in (east, south, west):
            player.choose_card = (  # type: ignore[method-assign]
                lambda trick, c, playable, _card=cards[player]: _card
            )

        view_calls = []

        class _SpyView:
            def request_card_action(self, player, trick, contract, playable):
                view_calls.append(player)
                return cards[player]

        round_.play_trick(view=_SpyView())

        assert human_calls == []  # choose_card bypassed for the human
        assert view_calls == [human]  # the view drove the human's turn
        assert cards[human] not in human.hand  # the chosen card was played


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

