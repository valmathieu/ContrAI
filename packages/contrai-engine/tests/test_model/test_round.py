"""Tests for the ``Round`` lifecycle orchestrator.

Covers the parts that stay in the orchestrator now that the trick loop is
driven by the immutable core :class:`PlayState`:

    * ``play_trick`` rejecting an illegal card with ``IllegalPlayError``
      (raised by the core state machine, no longer by the engine);
    * the mirror bookkeeping — the players' hands and ``current_trick``
      kept in lock-step with the authoritative ``play_state``;
    * card identity flowing unbroken from the seed to the playable set;
    * auction retention and play-state seeding across the lifecycle;
    * belote / rebelote detection and the announcement state machine;
    * the ``manage_bidding`` auto-pass UX promise (the human is never
      prompted when Pass is the only legal action).

The legal-play oracle itself lives in ``contrai-core``'s
``test_play_legality.py`` and the scoring grid in
``test_round_scoring.py``. The shared ``players`` fixture lives in
``conftest.py``.
"""

from __future__ import annotations

import pytest

from contrai_core import Hand
from contrai_core.auction import Auction
from contrai_core.bid import ContractBid, DoubleBid, PassBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.deck import Deck
from contrai_core.play import PlayState
from contrai_core.team import Team
from contrai_core.exceptions import IllegalPlayError, PlayRuleViolation
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.model.round import Round


# ---------------------------------------------------------------------------
# Scenario builders. The shared ``players`` fixture lives in ``conftest.py``.
# ---------------------------------------------------------------------------


class _StubDeck:
    """Deck stand-in for tests that drive ``play_trick`` to completion.

    ``play_trick`` returns the trick's cards to the deck at the end;
    the stub just swallows them so no real ``Deck`` state is needed.
    """

    def add_cards(self, cards):
        """Swallow the returned trick cards."""


def _make_round(players_dict, hands, contract, plays, deck=None):
    """Build a ``Round`` wired to the supplied state.

    Args:
        players_dict: mapping of seat letter → Player (from the
            ``players`` fixture).
        hands: mapping of seat letter → list of Cards in that player's
            hand.
        contract: a Contract object (provides trump) or None.
        plays: ordered list of (seat_letter, Card) tuples — the cards
            already played in the current trick.
        deck: optional deck object; tests that run ``play_trick`` to
            completion pass a ``_StubDeck`` so the end-of-trick
            ``add_cards`` call has something to land on.

    Returns:
        A Round whose ``current_trick`` reflects ``plays`` and whose
        ``players_order`` is the four players in N/E/S/W order.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    for seat, cards in hands.items():
        players_dict[seat].hand = Hand(cards)
    round_ = Round(order, dealer=players_dict["N"], deck=deck, round_number=1)
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
            lambda observation, _card=n_card: _card
        )
        players["E"].choose_card = (
            lambda observation, _card=e_illegal: _card
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
                lambda observation, _card=cards[player]: _card
            )

        view_calls = []

        class _SpyView:
            def request_card_action(self, player, trick, contract, playable):
                """Record who was prompted and return the scripted card."""
                view_calls.append(player)
                return cards[player]

        round_.play_trick(view=_SpyView())

        assert human_calls == []  # choose_card bypassed for the human
        assert view_calls == [human]  # the view drove the human's turn
        assert cards[human] not in human.hand  # the chosen card was played


# ---------------------------------------------------------------------------
# PlayState-driven loop: seeding, hand mirroring, and card identity
# ---------------------------------------------------------------------------


class TestSyncHandsMirrorsPlayState:
    """After each play the players' hands are re-mirrored from the
    authoritative ``play_state``: same content, same order, same ``Hand``
    object identity."""

    def test_hands_mirror_play_state_and_keep_hand_identity(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        # Each seat holds the heart it will play plus a distinct spare, so
        # the hands are non-empty after the trick and the mirror content is
        # observable.
        played = {
            "N": Card(Suit.HEARTS, Rank.KING),
            "E": Card(Suit.HEARTS, Rank.SEVEN),
            "S": Card(Suit.HEARTS, Rank.EIGHT),
            "W": Card(Suit.HEARTS, Rank.NINE),
        }
        spares = {
            "N": Card(Suit.DIAMONDS, Rank.KING),
            "E": Card(Suit.DIAMONDS, Rank.QUEEN),
            "S": Card(Suit.DIAMONDS, Rank.JACK),
            "W": Card(Suit.DIAMONDS, Rank.TEN),
        }
        hands = {seat: [played[seat], spares[seat]] for seat in played}
        round_ = _make_round(players, hands, contract, [], deck=_StubDeck())
        for seat, card in played.items():
            players[seat].choose_card = (
                lambda observation, _card=card: _card
            )

        # Capture the Hand object identities before the trick runs.
        original_hands = {seat: players[seat].hand for seat in played}

        round_.play_trick()

        for seat in played:
            player = players[seat]
            # The same Hand object, mutated in place — never reassigned.
            assert player.hand is original_hands[seat]
            authoritative = round_.play_state.hand_of(player)
            # Content and order agree with the authoritative state, and the
            # played heart is gone.
            assert list(player.hand) == list(authoritative)
            assert list(player.hand) == [spares[seat]]


class TestCardIdentityFlowsFromSeed:
    """The playable set handed to a strategy holds the very ``Card``
    objects seeded into ``play_state`` — no copy or reconstruction — so
    identity-matching call sites (the view) keep working."""

    def test_playables_are_the_seeded_card_objects(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        # N leads, so every card in N's hand is legal — the playable set is
        # N's whole hand and identity is easy to assert end-to-end.
        n_cards = [Card(Suit.HEARTS, Rank.KING), Card(Suit.CLUBS, Rank.ACE)]
        hands = {
            "N": n_cards,
            "E": [Card(Suit.HEARTS, Rank.SEVEN)],
            "S": [Card(Suit.HEARTS, Rank.EIGHT)],
            "W": [Card(Suit.HEARTS, Rank.NINE)],
        }
        round_ = _make_round(players, hands, contract, [], deck=_StubDeck())

        captured: dict[str, list] = {}

        def n_choose(observation):
            captured["playable"] = observation.legal_cards
            return observation.legal_cards[0]

        players["N"].choose_card = n_choose
        for seat in ("E", "S", "W"):
            card = hands[seat][0]
            players[seat].choose_card = (
                lambda observation, _card=card: _card
            )

        # The exact Card objects the seed will draw from N's hand.
        seeded = list(players["N"].hand)

        round_.play_trick()

        # Same objects, same order — matched by identity, not equality.
        assert len(captured["playable"]) == len(seeded)
        assert all(a is b for a, b in zip(captured["playable"], seeded))


class TestAuctionRetention:
    """``manage_bidding`` retains the terminal auction on the round."""

    def test_round_keeps_the_auction_that_set_the_contract(self, players):
        w, n, e, s = players["W"], players["N"], players["E"], players["S"]
        scripted = {
            n: [ContractBid(n, 80, Suit.HEARTS)],
            e: [PassBid(e)],
            s: [PassBid(s)],
            w: [PassBid(w)],
        }
        for ai, choices in scripted.items():
            queue = list(choices)
            ai.choose_bid = lambda _auction, _p=ai, _q=queue: (
                _q.pop(0) if _q else PassBid(_p)
            )

        # A capture view anchors the identity: the auction present when the
        # contract was established is the very object retained afterward.
        captured: dict[str, object] = {}

        class _CaptureView:
            def on_contract_established(self, round_):
                """Capture the auction object live at contract time."""
                captured["auction"] = round_.auction

        round_ = _empty_round(players)  # order N, E, S, W
        contract = round_.manage_bidding(view=_CaptureView())

        assert contract is not None
        assert round_.auction is not None
        assert round_.auction is captured["auction"]
        assert round_.auction.is_terminal()
        assert round_.auction.contract() == contract


class TestPlayStateSeeding:
    """``play_all_tricks`` seeds a *validated* start state; ``play_trick``
    lazy-seeds an *unvalidated* one when called directly."""

    def _script_first_playable(self, order):
        for player in order:
            player.choose_card = (
                lambda observation: observation.legal_cards[0]
            )

    def test_play_all_tricks_validates_the_deal(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=Deck(), round_number=1)
        round_.deal_cards()  # 8 distinct cards per seat
        round_.contract = _contract(players["N"], 100, Suit.SPADES)
        # Corrupt the deal: one seat now holds only 7 cards. A validated
        # seeding (PlayState.start) must reject it.
        players["N"].hand.remove(players["N"].hand[0])

        with pytest.raises(ValueError):
            round_.play_all_tricks()

    def test_play_trick_lazy_seeds_unvalidated_when_unseeded(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        # One card per seat — an invalid start deal, so a validated seeding
        # would raise. Lazy-seeding uses the bare constructor and proceeds.
        cards = {
            "N": Card(Suit.HEARTS, Rank.KING),
            "E": Card(Suit.HEARTS, Rank.SEVEN),
            "S": Card(Suit.HEARTS, Rank.EIGHT),
            "W": Card(Suit.HEARTS, Rank.NINE),
        }
        round_ = _make_round(
            players,
            {seat: [card] for seat, card in cards.items()},
            contract,
            [],
            deck=_StubDeck(),
        )
        for seat, card in cards.items():
            players[seat].choose_card = (
                lambda observation, _card=card: _card
            )

        assert round_.play_state is None

        round_.play_trick()

        # The state was seeded and advanced by exactly one trick's worth of
        # plays — no exception despite the sub-8-card hands.
        assert round_.play_state is not None
        assert len(round_.play_state.plays) == 4
        assert round_.play_state.trick_number == 1


class TestPlayThroughReachesTerminal:
    """A full ``play_all_tricks`` drives the ``play_state`` to a terminal
    state: 8 completed tricks and every card played."""

    def test_full_round_is_terminal_after_eight_tricks(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=Deck(), round_number=1)
        round_.deal_cards()
        round_.contract = _contract(players["N"], 100, Suit.SPADES)
        for player in order:
            player.choose_card = (
                lambda observation: observation.legal_cards[0]
            )

        round_.play_all_tricks()

        assert round_.play_state.is_terminal()
        assert len(round_.play_state.completed_tricks) == 8
        assert len(round_.tricks) == 8
        for player in order:
            assert len(player.hand) == 0


# ---------------------------------------------------------------------------
# The observation handed to each AI seat
# ---------------------------------------------------------------------------


class TestPlayTrickHandsObservation:
    """``play_trick`` hands each AI seat a frozen ``PlayObservation``
    projected from the authoritative play state, carrying that seat's legal
    cards, the public trick-so-far, and the retained auction's bids."""

    def test_observation_matches_play_state_and_auction(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        cards = {
            "N": Card(Suit.HEARTS, Rank.KING),
            "E": Card(Suit.HEARTS, Rank.SEVEN),
            "S": Card(Suit.HEARTS, Rank.EIGHT),
            "W": Card(Suit.HEARTS, Rank.NINE),
        }
        round_ = _make_round(
            players,
            {seat: [card] for seat, card in cards.items()},
            contract,
            [],
            deck=_StubDeck(),
        )

        # Retain a real terminal auction on the round: its bids must ride
        # along on every observation.
        auction = Auction.empty()
        auction = auction.apply(ContractBid(players["N"], 100, Suit.SPADES))
        for seat in ("E", "S", "W"):
            auction = auction.apply(PassBid(players[seat]))
        round_.auction = auction

        seen: dict[str, object] = {}

        def _record(seat):
            def choose(observation, _seat=seat):
                seen[_seat] = observation
                return observation.legal_cards[0]
            return choose

        for seat in ("N", "E", "S", "W"):
            players[seat].choose_card = _record(seat)

        round_.play_trick()

        # Every seat saw the retained auction's bids.
        for seat in ("N", "E", "S", "W"):
            assert seen[seat].bids == auction.bids
            assert seen[seat].completed_tricks == ()

        # The in-progress trick grows one play per seat, in play order.
        assert [c for _, c in seen["N"].current_trick] == []
        assert [c for _, c in seen["E"].current_trick] == [cards["N"]]
        assert [c for _, c in seen["S"].current_trick] == [
            cards["N"], cards["E"]
        ]
        assert [c for _, c in seen["W"].current_trick] == [
            cards["N"], cards["E"], cards["S"]
        ]

        # Legal cards come straight from the play state (each seat holds one).
        assert list(seen["N"].legal_cards) == [cards["N"]]
        assert list(seen["E"].legal_cards) == [cards["E"]]

    def test_bids_default_to_empty_when_no_auction_retained(self, players):
        contract = _contract(players["N"], 100, Suit.SPADES)
        cards = {
            "N": Card(Suit.HEARTS, Rank.KING),
            "E": Card(Suit.HEARTS, Rank.SEVEN),
            "S": Card(Suit.HEARTS, Rank.EIGHT),
            "W": Card(Suit.HEARTS, Rank.NINE),
        }
        round_ = _make_round(
            players,
            {seat: [card] for seat, card in cards.items()},
            contract,
            [],
            deck=_StubDeck(),
        )
        assert round_.auction is None  # nothing retained

        seen: list = []
        for seat in ("N", "E", "S", "W"):
            players[seat].choose_card = (
                lambda observation: (
                    seen.append(observation) or observation.legal_cards[0]
                )
            )

        round_.play_trick()

        assert seen  # the AI path ran
        for observation in seen:
            assert observation.bids == ()


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

        # Pre-seed each AI's choose_bid via a scripted queue of concrete
        # :class:`Bid` objects, matching the ``Bid``-typed signature of
        # ``Player.choose_bid``. Each seat's bids are attached to that
        # seat so ``Auction`` records the right player.
        w, n, e = players["W"], players["N"], players["E"]
        scripted = {
            w: [ContractBid(w, 100, Suit.HEARTS), PassBid(w), PassBid(w), PassBid(w)],
            n: [DoubleBid(n), PassBid(n), PassBid(n), PassBid(n)],
            e: [PassBid(e), PassBid(e), PassBid(e), PassBid(e)],
        }
        for ai, choices in scripted.items():
            queue = list(choices)
            ai.choose_bid = lambda _auction, _p=ai, _q=queue: (
                _q.pop(0) if _q else PassBid(_p)
            )

        # Stub view: records request_bid_action calls. Asserting it
        # is NEVER called is the whole point of the test.
        prompts = []

        class _View:
            def request_bid_action(self, player, auction):
                """Record the prompt (the test asserts none ever happens)."""
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

