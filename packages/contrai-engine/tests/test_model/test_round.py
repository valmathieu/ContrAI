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
from contrai_core.bid import ContractBid, DoubleBid, PassBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.deck import Deck
from contrai_core.play import Play, PlayState
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
        pass


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
                lambda trick, c, playable, _card=card: _card
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

        def n_choose(trick, c, playable):
            captured["playable"] = playable
            return playable[0]

        players["N"].choose_card = n_choose
        for seat in ("E", "S", "W"):
            card = hands[seat][0]
            players[seat].choose_card = (
                lambda trick, c, playable, _card=card: _card
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
                lambda trick, c, playable: playable[0]
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
                lambda trick, c, playable, _card=card: _card
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
                lambda trick, c, playable: playable[0]
            )

        round_.play_all_tricks()

        assert round_.play_state.is_terminal()
        assert len(round_.play_state.completed_tricks) == 8
        assert len(round_.tricks) == 8
        for player in order:
            assert len(player.hand) == 0


# ---------------------------------------------------------------------------
# Card-tracking fan-out (play_trick) and per-deal reset (deal_cards)
# ---------------------------------------------------------------------------


class TestPlayTrickFeedsCardTrackers:
    """``play_trick`` fans every landing card out to every AI seat's
    tracker, with the sound (compelled-only) trump-void inference."""

    def _script(self, players, cards):
        """Monkey-patch each seat's ``choose_card`` to its scripted card."""
        for seat, card in cards.items():
            players[seat].choose_card = (
                lambda trick, c, playable, _card=card: _card
            )

    def test_full_trick_updates_every_ai_tracker(self, players):
        """All four seats follow the led suit: every tracker records all
        four cards and nobody is marked void in trump."""
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
        self._script(players, cards)

        round_.play_trick()

        for player in round_.players_order:
            assert player.cardplay._fallen_cards[Suit.HEARTS] == {
                Rank.KING, Rank.SEVEN, Rank.EIGHT, Rank.NINE
            }
            assert player.cardplay._players_without_trump == set()

    def test_compelled_discard_marks_void_for_every_tracker(self, players):
        """E cannot follow hearts and cannot trump while the opponents
        are master — the compelled discard proves E holds no trump, and
        every tracker learns it."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        cards = {
            "N": Card(Suit.HEARTS, Rank.ACE),
            "E": Card(Suit.CLUBS, Rank.SEVEN),  # no hearts, no trump
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
        self._script(players, cards)

        round_.play_trick()

        for player in round_.players_order:
            assert player.cardplay._players_without_trump == {players["E"]}

    def test_voluntary_discard_behind_master_partner_is_not_marked_void(
        self, players
    ):
        """S discards a club while actually holding a trump — legal
        because partner N is master. The card is recorded everywhere but
        S must NOT be marked void: that is exactly the false positive
        the ``partner_was_master`` flag exists to avoid."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        s_discard = Card(Suit.CLUBS, Rank.SEVEN)
        cards = {
            "N": Card(Suit.HEARTS, Rank.ACE),  # master lead
            "E": Card(Suit.HEARTS, Rank.SEVEN),
            "S": s_discard,
            "W": Card(Suit.HEARTS, Rank.EIGHT),
        }
        hands = {
            "N": [cards["N"]],
            "E": [cards["E"]],
            # S holds a trump too — the discard is voluntary.
            "S": [s_discard, Card(Suit.SPADES, Rank.SEVEN)],
            "W": [cards["W"]],
        }
        round_ = _make_round(players, hands, contract, [], deck=_StubDeck())
        self._script(players, cards)

        round_.play_trick()

        for player in round_.players_order:
            assert players["S"] not in player.cardplay._players_without_trump
            assert Rank.SEVEN in player.cardplay._fallen_cards[Suit.CLUBS]

    def test_human_seat_is_skipped_but_its_card_is_tracked(self):
        """The human seat has no tracker (hasattr gate) yet the card it
        plays still lands in every AI tracker."""
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

        for player in (east, south, west):
            player.choose_card = (  # type: ignore[method-assign]
                lambda trick, c, playable, _card=cards[player]: _card
            )

        class _StubView:
            def request_card_action(self, player, trick, contract, playable):
                return cards[player]

        round_.play_trick(view=_StubView())

        assert not hasattr(human, 'update_card_tracking')
        for ai in (east, south, west):
            assert Rank.KING in ai.cardplay._fallen_cards[Suit.HEARTS]


class TestDealCardsResetsCardTracking:
    """``deal_cards`` resets every AI seat's card tracking — player
    objects persist across rounds, so leftover state from the previous
    round must be zeroed at each deal."""

    def _poison(self, ai_players, scapegoat):
        """Fill each AI's tracking state with leftover-looking data."""
        for player in ai_players:
            player.cardplay._fallen_cards[Suit.HEARTS].add(Rank.ACE)
            player.cardplay._players_without_trump.add(scapegoat)

    def test_deal_resets_poisoned_trackers(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=Deck(), round_number=2)
        self._poison(order, players["E"])

        round_.deal_cards()

        for player in order:
            assert all(
                len(ranks) == 0
                for ranks in player.cardplay._fallen_cards.values()
            )
            assert player.cardplay._players_without_trump == set()
            assert len(player.hand) == 8

    def test_deal_skips_seats_without_a_tracker(self, players):
        human = HumanPlayer("H", "North")
        human.team = players["N"].team  # Round.__init__ maps teams by seat
        ais = [players[s] for s in ("E", "S", "W")]
        order = [human] + ais
        round_ = Round(order, dealer=human, deck=Deck(), round_number=1)
        self._poison(ais, players["E"])

        round_.deal_cards()  # must not raise on the tracker-less human

        for player in ais:
            assert all(
                len(ranks) == 0
                for ranks in player.cardplay._fallen_cards.values()
            )
            assert player.cardplay._players_without_trump == set()


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

