"""Tests for the ``Round`` lifecycle orchestrator.

Covers the parts that stay in the orchestrator now that the trick loop is
driven by the immutable core :class:`PlayState`:

    * ``play_trick`` rejecting an illegal card with ``IllegalPlayError``
      (raised by the core state machine, no longer by the engine);
    * the mirror bookkeeping — the players' hands kept in lock-step
      with the authoritative ``play_state``;
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

import logging

import pytest

from contrai_core import Hand, Position
from contrai_core.auction import Auction
from contrai_core.bid import ContractBid, DoubleBid, PassBid, SlamLevel
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.deck import Deck
from contrai_core.play import PlayState
from contrai_core.rule_config import AllTrumpBelote, RuleConfig
from contrai_core.team import Team
from contrai_core.team_side import TeamSide
from contrai_core.exceptions import IllegalPlayError, PlayRuleViolation
from contrai_core.types import CONTRACT_SUITS, Rank, Suit, TrumpVariant

from contrai_engine.model.player import (
    AiPlayer,
    BidDecision,
    CardDecision,
    HumanPlayer,
    Rationale,
)
from contrai_engine.model.round import Round


# ---------------------------------------------------------------------------
# Scenario builders. The shared ``players`` fixture lives in ``conftest.py``.
# ---------------------------------------------------------------------------


#: The rationale every stub below attaches. The decision *shape* is what
#: ``Round`` unwraps; what a stub says about its reasoning is irrelevant to
#: the lifecycle under test, so one shared value keeps the stubs readable.
_STUB = Rationale("stub", "test double")


def _card_choice(card):
    """Wrap ``card`` as the :class:`CardDecision` ``Round`` now expects."""
    return CardDecision(card, _STUB)


def _bid_choice(bid):
    """Wrap ``bid`` as the :class:`BidDecision` ``Round`` now expects."""
    return BidDecision(bid, _STUB)


class _StubDeck:
    """Deck stand-in for tests that drive ``play_trick`` to completion.

    ``play_trick`` returns the trick's cards to the deck at the end;
    the stub just swallows them so no real ``Deck`` state is needed.
    """

    def add_cards(self, cards):
        """Swallow the returned trick cards."""


def _make_round(players_dict, hands, contract, deck=None, rules=None):
    """Build a ``Round`` wired to the supplied state.

    ``play_trick`` seeds the play state lazily from the hands, so the
    round needs nothing beyond the seating, the contract and the deal.

    Args:
        players_dict: mapping of seat letter → Player (from the
            ``players`` fixture).
        hands: mapping of seat letter → list of Cards in that player's
            hand.
        contract: a Contract object (provides trump) or None.
        deck: optional deck object; tests that run ``play_trick`` to
            completion pass a ``_StubDeck`` so the end-of-trick
            ``add_cards`` call has something to land on.
        rules: optional table ruleset; ``None`` leaves the Round on its
            own default (the §9 catalogue).

    Returns:
        A Round whose ``players_order`` is the four players in N/E/S/W
        order.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    for seat, cards in hands.items():
        players_dict[seat].hand = Hand(cards)
    round_ = Round(
        order, dealer=players_dict["N"], deck=deck, round_number=1, rules=rules
    )
    round_.contract = contract
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
        )
        # Scripted choices: N leads its only heart, E tries the illegal trump.
        players["N"].choose_card = (
            lambda observation, _card=n_card: _card_choice(_card)
        )
        players["E"].choose_card = (
            lambda observation, _card=e_illegal: _card_choice(_card)
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
        human = HumanPlayer("H", Position.NORTH)
        east = AiPlayer("E", Position.EAST)
        south = AiPlayer("S", Position.SOUTH)
        west = AiPlayer("W", Position.WEST)
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
                lambda observation, _card=cards[player]: _card_choice(_card)
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
        round_ = _make_round(players, hands, contract, deck=_StubDeck())
        for seat, card in played.items():
            players[seat].choose_card = (
                lambda observation, _card=card: _card_choice(_card)
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
        round_ = _make_round(players, hands, contract, deck=_StubDeck())

        captured: dict[str, list] = {}

        def n_choose(observation):
            captured["playable"] = observation.legal_cards
            return _card_choice(observation.legal_cards[0])

        players["N"].choose_card = n_choose
        for seat in ("E", "S", "W"):
            card = hands[seat][0]
            players[seat].choose_card = (
                lambda observation, _card=card: _card_choice(_card)
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
                _bid_choice(_q.pop(0) if _q else PassBid(_p))
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
                lambda observation: _card_choice(observation.legal_cards[0])
            )

    def test_play_all_tricks_validates_the_deal(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=Deck(), round_number=1)
        round_.deal_cards()  # 8 distinct cards per seat
        round_.contract = _contract(players["N"], 100, Suit.SPADES)
        # Corrupt the deal: one seat now holds only 7 cards. A validated
        # seeding (PlayState.start) must reject it.
        players["N"].hand.remove(next(iter(players["N"].hand)))

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
            deck=_StubDeck(),
        )
        for seat, card in cards.items():
            players[seat].choose_card = (
                lambda observation, _card=card: _card_choice(_card)
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
                lambda observation: _card_choice(observation.legal_cards[0])
            )

        round_.play_all_tricks()

        assert round_.play_state.is_terminal()
        assert len(round_.play_state.completed_tricks) == 8
        for player in order:
            assert len(player.hand) == 0


# ---------------------------------------------------------------------------
# The table ruleset reaches the play state
# ---------------------------------------------------------------------------


class TestRulesThreading:
    """The ``RuleConfig`` handed to a ``Round`` seeds every ``PlayState``
    it creates — through the validated start and the lazy seed alike."""

    def test_round_defaults_to_the_classic_ruleset(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=None, round_number=1)
        assert round_.rules == RuleConfig()

    def test_play_all_tricks_seeds_play_state_with_the_rules(self, players):
        rules = RuleConfig(target_score=1000)
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["N"], deck=Deck(), round_number=1, rules=rules
        )
        round_.deal_cards()
        round_.contract = _contract(players["N"], 100, Suit.SPADES)
        for player in order:
            player.choose_card = (
                lambda observation: _card_choice(observation.legal_cards[0])
            )

        round_.play_all_tricks()

        assert round_.play_state.rules is rules

    def test_play_trick_lazy_seed_carries_the_rules(self, players):
        rules = RuleConfig(target_score=1000)
        contract = _contract(players["N"], 100, Suit.SPADES)
        # One card per seat — the invalid-start deal the lazy seed exists
        # for, so this exercises the bare-constructor path, not start().
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
            deck=_StubDeck(),
            rules=rules,
        )
        for seat, card in cards.items():
            players[seat].choose_card = (
                lambda observation, _card=card: _card_choice(_card)
            )

        round_.play_trick()

        assert round_.play_state.rules is rules


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
                return _card_choice(observation.legal_cards[0])
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
            deck=_StubDeck(),
        )
        assert round_.auction is None  # nothing retained

        seen: list = []
        for seat in ("N", "E", "S", "W"):
            players[seat].choose_card = (
                lambda observation: _card_choice(
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


#: N pairs in ♠ and ♥, E pairs in ♣. Under the all-trump ``four`` regime
#: that is 2 belotes for N-S and 1 for E-W; under ``single`` only the
#: first pair announced in play marks, whichever team holds it.
_TWO_SIDED_BELOTE_HANDS = {
    "N": [
        Card(Suit.SPADES, Rank.KING),
        Card(Suit.SPADES, Rank.QUEEN),
        Card(Suit.HEARTS, Rank.KING),
        Card(Suit.HEARTS, Rank.QUEEN),
    ],
    "E": [
        Card(Suit.CLUBS, Rank.KING),
        Card(Suit.CLUBS, Rank.QUEEN),
        Card(Suit.DIAMONDS, Rank.KING),
    ],
    "S": [Card(Suit.DIAMONDS, Rank.QUEEN)],
    "W": [Card(Suit.SPADES, Rank.SEVEN)],
}


class TestBelotePairDetection:
    """``_detect_belote_pairs`` snapshots every K + Q pair at deal time."""

    def test_records_the_pair_when_present(self, players):
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
        )
        round_._detect_belote_pairs()
        assert round_.belote_pairs == {players["S"]: (Suit.HEARTS,)}

    def test_no_pair_when_split(self, players):
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
        )
        round_._detect_belote_pairs()
        assert round_.belote_pairs == {}

    def test_a_pair_outside_the_trump_suit_is_not_one(self, players):
        # Only the trump suit can carry a belote at a suit contract, so
        # the ♠ pair below is just two cards.
        contract = _contract(players["N"], 100, Suit.HEARTS)
        round_ = _make_round(
            players,
            {
                "N": [],
                "E": [],
                "S": [
                    Card(Suit.SPADES, Rank.KING),
                    Card(Suit.SPADES, Rank.QUEEN),
                ],
                "W": [],
            },
            contract,
        )
        round_._detect_belote_pairs()
        assert round_.belote_pairs == {}

    def test_no_pairs_at_no_trump(self, players):
        contract = _contract(players["N"], 100, TrumpVariant.NO_TRUMP)
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
        )
        round_._detect_belote_pairs()
        assert round_.belote_pairs == {}
        assert round_.belote_counts_by_side == {TeamSide.NS: 0, TeamSide.EW: 0}


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
        )
        round_._detect_belote_pairs()
        return round_

    def test_first_play_returns_belote(self, players):
        round_ = self._setup(players)
        card = Card(Suit.HEARTS, Rank.KING)
        assert round_._is_belote_event(players["S"], card) is True
        kind = round_._transition_belote_state(players["S"], card.suit)
        assert kind == "belote"
        assert round_.belote_state == {(players["S"], Suit.HEARTS): "belote"}
        assert round_.belote_order == [(players["S"], Suit.HEARTS)]

    def test_second_play_returns_rebelote(self, players):
        round_ = self._setup(players)
        round_._transition_belote_state(players["S"], Suit.HEARTS)
        kind = round_._transition_belote_state(players["S"], Suit.HEARTS)
        assert kind == "rebelote"
        assert round_.belote_state == {(players["S"], Suit.HEARTS): "rebelote"}
        # The pair is announced once, however many cards of it are played.
        assert round_.belote_order == [(players["S"], Suit.HEARTS)]

    def test_a_third_play_of_the_same_pair_is_inert(self, players):
        # Defensive: each card is unique, so this cannot happen in play.
        round_ = self._setup(players)
        round_._transition_belote_state(players["S"], Suit.HEARTS)
        round_._transition_belote_state(players["S"], Suit.HEARTS)
        assert round_._transition_belote_state(players["S"], Suit.HEARTS) is None

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
        # N plays K♥ — but N holds no pair.
        assert (
            round_._is_belote_event(players["N"], Card(Suit.HEARTS, Rank.KING))
            is False
        )


class TestAllTrumpBelote:
    """§6.6 — the three all-trump belote regimes, applied by the Round."""

    def _all_trump_round(self, players, regime):
        round_ = _make_round(
            players,
            _TWO_SIDED_BELOTE_HANDS,
            contract=_contract(players["N"], 100, TrumpVariant.ALL_TRUMP),
            rules=RuleConfig(
                extended_trump_choices=True, all_trump_belote=regime
            ),
        )
        round_._detect_belote_pairs()
        return round_

    def test_four_regime_detects_every_pair(self, players):
        # N holds K+Q of spades and of hearts; E holds K+Q of clubs.
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        assert round_.belote_pairs == {
            players["N"]: (Suit.SPADES, Suit.HEARTS),
            players["E"]: (Suit.CLUBS,),
        }
        assert round_.belote_counts_by_side == {TeamSide.NS: 2, TeamSide.EW: 1}

    def test_none_regime_detects_nothing(self, players):
        # A table playing `none` has no belote to announce at all — not
        # one that is announced and then fails to score.
        round_ = self._all_trump_round(players, AllTrumpBelote.NONE)
        assert round_.belote_pairs == {}
        assert round_.belote_counts_by_side == {TeamSide.NS: 0, TeamSide.EW: 0}
        assert round_._is_belote_event(
            players["N"], Card(Suit.SPADES, Rank.KING)
        ) is False

    def test_single_regime_scores_only_the_first_announced(self, players):
        # Both sides hold a pair and both announce; E announces first, so
        # E-W marks the 20 and N-S marks nothing (§6.6).
        round_ = self._all_trump_round(players, AllTrumpBelote.SINGLE)
        assert set(round_.belote_pairs) == {players["N"], players["E"]}
        round_._transition_belote_state(players["E"], Suit.CLUBS)
        round_._transition_belote_state(players["N"], Suit.SPADES)
        assert round_.belote_order[0] == (players["E"], Suit.CLUBS)
        assert round_.belote_counts_by_side == {TeamSide.NS: 0, TeamSide.EW: 1}

    def test_single_regime_follows_announcement_order_not_seat_order(
        self, players
    ):
        # The mirror image of the previous case: N announces first and
        # marks, even though the pairs held are identical. Nothing about
        # the seating decides it.
        round_ = self._all_trump_round(players, AllTrumpBelote.SINGLE)
        round_._transition_belote_state(players["N"], Suit.HEARTS)
        round_._transition_belote_state(players["E"], Suit.CLUBS)
        assert round_.belote_counts_by_side == {TeamSide.NS: 1, TeamSide.EW: 0}

    def test_single_regime_still_announces_for_every_holder(self, players):
        # The second holder announces — it is a narrative event — and
        # scores nothing. Both pairs reach "rebelote" in belote_state.
        round_ = self._all_trump_round(players, AllTrumpBelote.SINGLE)
        for holder, suit in ((players["E"], Suit.CLUBS),
                             (players["N"], Suit.SPADES)):
            round_._transition_belote_state(holder, suit)
            round_._transition_belote_state(holder, suit)
        assert set(round_.belote_state.values()) == {"rebelote"}
        assert len(round_.belote_state) == 2

    def test_nothing_announced_yet_scores_nothing_under_single(self, players):
        # `single` marks the first pair *announced in play*; before any
        # card is played there is no such pair.
        round_ = self._all_trump_round(players, AllTrumpBelote.SINGLE)
        assert round_.belote_counts_by_side == {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_four_regime_scores_without_waiting_for_announcements(
        self, players
    ):
        # Unlike `single`, `four` reads the pairs held, not the order they
        # were announced in — so it is already correct at deal time.
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        assert round_.belote_counts_by_side == {TeamSide.NS: 2, TeamSide.EW: 1}

    def test_a_holders_king_in_a_suit_they_do_not_pair_is_not_an_event(
        self, players
    ):
        # At all trump every K/Q is a trump K/Q, so the event predicate
        # must key on the *pair*, not on trumpness. E holds the ♦K but not
        # the ♦Q, so playing it announces nothing.
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        assert round_._is_belote_event(
            players["E"], Card(Suit.DIAMONDS, Rank.KING)
        ) is False
        assert round_._is_belote_event(
            players["E"], Card(Suit.CLUBS, Rank.KING)
        ) is True

    def test_a_suit_contract_is_unaffected_by_the_regime(self, players):
        for regime in AllTrumpBelote:
            round_ = _make_round(
                players,
                _TWO_SIDED_BELOTE_HANDS,
                contract=_contract(players["N"], 100, Suit.HEARTS),
                rules=RuleConfig(all_trump_belote=regime),
            )
            round_._detect_belote_pairs()
            # Only ♥ can carry a belote here, so N's ♥K + ♥Q is the one
            # pair that exists and the regime knob never gets a say.
            assert round_.belote_pairs == {players["N"]: (Suit.HEARTS,)}
            assert round_.belote_counts_by_side == {
                TeamSide.NS: 1, TeamSide.EW: 0
            }


class TestAnnouncedBelotes:
    """``announced_belotes`` — the pairs announced *and* marking, in order.

    The display counterpart of ``_scoring_belotes``: it answers what the
    table has actually seen so far, where the scorer answers what will
    be marked at the end. The two agree once every card is played.
    """

    def _all_trump_round(self, players, regime):
        round_ = _make_round(
            players,
            _TWO_SIDED_BELOTE_HANDS,
            contract=_contract(players["N"], 100, TrumpVariant.ALL_TRUMP),
            rules=RuleConfig(
                extended_trump_choices=True, all_trump_belote=regime
            ),
        )
        round_._detect_belote_pairs()
        return round_

    def test_nothing_announced_yet_is_empty(self, players):
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        assert round_.announced_belotes == ()

    def test_four_regime_reports_every_announced_pair(self, players):
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        round_._transition_belote_state(players["N"], Suit.SPADES)
        round_._transition_belote_state(players["E"], Suit.CLUBS)
        assert round_.announced_belotes == (
            (players["N"], Suit.SPADES),
            (players["E"], Suit.CLUBS),
        )

    def test_four_regime_hides_a_held_but_unannounced_pair(self, players):
        # N holds ♠ and ♥ but has only announced ♠. The ♥ pair is still
        # hidden information — ``_scoring_belotes`` counts it because the
        # scorer may, but nothing the human sees is allowed to.
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        round_._transition_belote_state(players["N"], Suit.SPADES)
        assert (players["N"], Suit.HEARTS) in round_._scoring_belotes()
        assert round_.announced_belotes == ((players["N"], Suit.SPADES),)

    def test_it_follows_announcement_order_not_seat_order(self, players):
        round_ = self._all_trump_round(players, AllTrumpBelote.FOUR)
        round_._transition_belote_state(players["E"], Suit.CLUBS)
        round_._transition_belote_state(players["N"], Suit.HEARTS)
        assert round_.announced_belotes == (
            (players["E"], Suit.CLUBS),
            (players["N"], Suit.HEARTS),
        )

    def test_single_regime_reports_only_the_first_announced(self, players):
        # Both sides announce; only E's pair marks, so only E's is shown.
        round_ = self._all_trump_round(players, AllTrumpBelote.SINGLE)
        round_._transition_belote_state(players["E"], Suit.CLUBS)
        round_._transition_belote_state(players["N"], Suit.SPADES)
        assert round_.announced_belotes == ((players["E"], Suit.CLUBS),)

    def test_single_regime_is_unmoved_by_a_rebelote(self, players):
        # The second card of the *same* pair advances belote_state but
        # appends nothing, so the marking pair does not change.
        round_ = self._all_trump_round(players, AllTrumpBelote.SINGLE)
        round_._transition_belote_state(players["N"], Suit.SPADES)
        round_._transition_belote_state(players["N"], Suit.SPADES)
        assert round_.announced_belotes == ((players["N"], Suit.SPADES),)

    def test_none_regime_reports_nothing(self, players):
        round_ = self._all_trump_round(players, AllTrumpBelote.NONE)
        assert round_.announced_belotes == ()

    def test_a_suit_contract_reports_the_announced_pair(self, players):
        round_ = _make_round(
            players,
            _TWO_SIDED_BELOTE_HANDS,
            contract=_contract(players["N"], 100, Suit.HEARTS),
        )
        round_._detect_belote_pairs()
        assert round_.announced_belotes == ()
        round_._transition_belote_state(players["N"], Suit.HEARTS)
        assert round_.announced_belotes == ((players["N"], Suit.HEARTS),)

    def test_no_contract_reports_nothing(self, players):
        round_ = _make_round(players, _TWO_SIDED_BELOTE_HANDS, contract=None)
        assert round_.announced_belotes == ()


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


def _empty_round(players_dict, rules=None):
    """A Round with no contract / no trick — enough for bidding helpers.

    Args:
        players_dict: mapping of seat letter → Player.
        rules: optional table ruleset; ``None`` leaves the Round on its
            own default (the §9 catalogue).
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    return Round(
        order, dealer=players_dict["N"], deck=None, round_number=1, rules=rules
    )


class TestAuctionRuleset:
    """The auction runs under the round's table ruleset (§9.2)."""

    def test_the_auction_runs_under_the_round_ruleset(self, players):
        rules = RuleConfig(extended_trump_choices=True)
        round_ = _empty_round(players, rules=rules)
        round_.manage_bidding()
        assert round_.auction.rules is rules

    def test_the_default_auction_offers_no_variants(self, players):
        # Asked of the *finished* auction so the assertion holds whatever
        # the AI seats bid — the offered trump set is a table rule, not an
        # auction-state one.
        round_ = _empty_round(players)
        round_.manage_bidding()
        offered = {
            b.suit
            for b in Auction.empty(rules=round_.rules).legal_actions(players["N"])
            if isinstance(b, ContractBid)
        }
        assert offered == set(Suit)

    def test_an_extended_auction_offers_both_variants(self, players):
        rules = RuleConfig(extended_trump_choices=True)
        round_ = _empty_round(players, rules=rules)
        round_.manage_bidding()
        offered = {
            b.suit
            for b in Auction.empty(rules=round_.rules).legal_actions(players["N"])
            if isinstance(b, ContractBid)
        }
        assert offered == set(CONTRACT_SUITS)


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
        human = HumanPlayer("You", Position.SOUTH)
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
                _bid_choice(_q.pop(0) if _q else PassBid(_p))
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


# ---------------------------------------------------------------------------
# Debug-logging diagnostics (stdlib logging, model-side)
# ---------------------------------------------------------------------------


class TestContractFixedLogging:
    """``manage_bidding`` logs the fixed contract once bidding closes."""

    def test_contract_fixed_logs_at_debug(self, players, caplog):
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
                _bid_choice(_q.pop(0) if _q else PassBid(_p))
            )

        round_ = _empty_round(players)  # order N, E, S, W

        with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
            contract = round_.manage_bidding()

        assert contract is not None
        records = [
            record for record in caplog.records
            if record.name == "contrai_engine.model.round.round"
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.DEBUG
        assert records[0].getMessage() == "contract fixed: 80 Hearts by N"

    def test_all_pass_does_not_log_contract_fixed(self, players, caplog):
        """An all-passed auction never fixes a contract, so the
        "contract fixed" line must never appear."""
        for ai in players.values():
            ai.choose_bid = lambda _auction, _p=ai: _bid_choice(PassBid(_p))

        round_ = _empty_round(players)

        with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
            contract = round_.manage_bidding()

        assert contract is None
        assert not any(
            "contract fixed" in record.getMessage() for record in caplog.records
        )


class TestTrickCompletedLogging:
    """``play_trick`` logs the winner and point total once a trick closes."""

    def _all_trump_trick(self, players):
        """A one-round-of-spades trick: every seat holds a single trump
        card, so follow-suit is trivial and the point/winner arithmetic is
        easy to state by hand.

        J♠ (20) + 9♠ (14) + A♠ (11) + 7♠ (0) = 45 trump points; the Jack of
        trump outranks every other trump card, so North (the leader) wins.
        """
        contract = _contract(players["N"], 100, Suit.SPADES)
        hands = {
            "N": [Card(Suit.SPADES, Rank.JACK)],
            "E": [Card(Suit.SPADES, Rank.NINE)],
            "S": [Card(Suit.SPADES, Rank.ACE)],
            "W": [Card(Suit.SPADES, Rank.SEVEN)],
        }
        round_ = _make_round(players, hands, contract, deck=_StubDeck())
        for seat, cards in hands.items():
            players[seat].choose_card = (
                lambda observation, _card=cards[0]: _card_choice(_card)
            )
        return round_

    def test_trick_completed_logs_winner_and_points_at_debug(
        self, players, caplog
    ):
        round_ = self._all_trump_trick(players)

        with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
            round_.play_trick()

        records = [
            record for record in caplog.records
            if record.name == "contrai_engine.model.round.round"
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.DEBUG
        assert records[0].getMessage() == (
            "trick 1 complete: winner North, 45 points"
        )

    def test_trick_completed_emits_no_debug_log_by_default(
        self, players, caplog
    ):
        """Test that no trick-completed record is captured at the default
        log level. A caplog assertion only observes the emitted record, so
        this can't distinguish "the guard skipped the points sum" from
        "the sum ran but the disabled logger call no-opped it" — it
        confirms the observable, back-compat-relevant behavior: nothing is
        emitted for contrai_engine.model.round.round when DEBUG is not
        active."""
        round_ = self._all_trump_trick(players)

        round_.play_trick()

        assert not any(
            record.name == "contrai_engine.model.round.round"
            for record in caplog.records
        )



class TestSoloSlamGivesTheLead:
    """§6 / §9.5 — off by default; on, the declarer opens trick 1."""

    def _round(self, players, contract_value, rules):
        """A round dealt in N/E/S/W order whose contract belongs to E."""
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["N"], deck=Deck(), round_number=1, rules=rules
        )
        round_.deal_cards()
        round_.contract = _contract(players["E"], contract_value, Suit.SPADES)
        return round_

    def test_the_seat_after_the_dealer_leads_by_default(self, players):
        round_ = self._round(players, SlamLevel.SOLO_SLAM, RuleConfig())
        seating, _hands = round_._play_seating()
        assert seating[0] is round_.players_order[0]

    def test_the_declarer_leads_when_the_option_is_on(self, players):
        rules = RuleConfig(solo_slam_gives_the_lead=True)
        round_ = self._round(players, SlamLevel.SOLO_SLAM, rules)
        seating, hands = round_._play_seating()
        assert seating[0] is players["E"]
        # A rotation, not a rebuild: the cyclic order is preserved, so
        # play still runs in the table's direction.
        assert list(seating) == [
            players["E"], players["S"], players["W"], players["N"]
        ]
        # Hands stay parallel to seats.
        assert hands[0] == tuple(players["E"].hand)

    def test_a_plain_slam_does_not_take_the_lead(self, players):
        rules = RuleConfig(solo_slam_gives_the_lead=True)
        round_ = self._round(players, SlamLevel.SLAM, rules)
        seating, _hands = round_._play_seating()
        assert seating[0] is round_.players_order[0]

    def test_a_numeric_contract_does_not_take_the_lead(self, players):
        rules = RuleConfig(solo_slam_gives_the_lead=True)
        round_ = self._round(players, 100, rules)
        seating, _hands = round_._play_seating()
        assert seating[0] is round_.players_order[0]

    def test_play_all_tricks_seeds_the_state_on_the_declarer(self, players):
        rules = RuleConfig(solo_slam_gives_the_lead=True)
        round_ = self._round(players, SlamLevel.SOLO_SLAM, rules)
        for player in round_.players_order:
            player.choose_card = (
                lambda observation: _card_choice(observation.legal_cards[0])
            )
        round_.play_all_tricks()
        assert round_.play_state.is_terminal()
        assert round_.play_state.plays[0].player is players["E"]
