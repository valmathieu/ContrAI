"""Tests for :class:`PlayObservation`, the play-phase imperfect-information view.

``PlayState.observe`` projects the full state — which holds every seat's
hand — down to what a single player is allowed to see. These tests pin:
the field set (own hand only, nothing leaking another seat's cards), that
``legal_cards`` matches :meth:`PlayState.legal_actions` by object identity,
that ``bids`` passes through untouched, the five derived properties, that
the observation is immutable, and that the trick records are sealed to
``ObservedPlay`` ``(position, card)`` pairs with no live ``BasePlayer``
reachable through them. The shared ``players`` fixture lives in
``conftest.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from contrai_core import (
    BasePlayer,
    Card,
    Contract,
    PlayState,
    Position,
    Rank,
    Suit,
    TrumpVariant,
)
from contrai_core.bid import ContractBid, PassBid
from contrai_core.play import ObservedPlay, Play, PlayObservation

# Seat → suit assignment for a deterministic full deal: each player holds
# one whole suit, so cross-seat leakage is trivial to detect and trick
# winners are easy to reason about.
_SEAT_SUITS = {
    "N": Suit.SPADES,
    "E": Suit.HEARTS,
    "S": Suit.DIAMONDS,
    "W": Suit.CLUBS,
}
_ORDER = ("N", "E", "S", "W")

_EXPECTED_FIELDS = {
    "player",
    "hand",
    "contract",
    "bids",
    "completed_tricks",
    "current_trick",
    "legal_cards",
}


def _deal(
    players_dict: dict[str, BasePlayer], trump: Suit = Suit.HEARTS
) -> tuple[
    Contract, tuple[BasePlayer, ...], tuple[tuple[Card, ...], ...], dict[str, list[Card]]
]:
    """Build a valid full deal: 4 players, one whole suit each, a contract."""
    ranks = list(Rank)
    by_seat = {
        seat: [Card(suit, r) for r in ranks] for seat, suit in _SEAT_SUITS.items()
    }
    seating = tuple(players_dict[s] for s in _ORDER)
    hands = tuple(tuple(by_seat[s]) for s in _ORDER)
    contract = Contract(ContractBid(players_dict["N"], 100, trump))
    return contract, seating, hands, by_seat


def _play_first_trick(players_dict: dict[str, BasePlayer]) -> PlayState:
    """Play out trick 0 legally: North leads a spade, East ruffs and wins.

    East is void in spades, so its trump heart wins the trick — a
    trump-beats-lead scenario for the ``current_winner`` tests.
    """
    contract, seating, hands, by_seat = _deal(players_dict)
    state = PlayState.start(contract, seating, hands)
    for seat in _ORDER:
        state = state.apply(Play(players_dict[seat], by_seat[seat][0]))
    return state


# ---------------------------------------------------------------------------
# Own-hand-only
# ---------------------------------------------------------------------------


class TestOwnHandOnly:
    def test_field_set_is_exactly_the_seven_specified(self):
        assert set(PlayObservation.__dataclass_fields__) == _EXPECTED_FIELDS

    def test_hand_matches_hand_of_and_leaks_no_other_seat(self, players):
        contract, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)

        for seat in _ORDER:
            player = players[seat]
            obs = state.observe(player)
            assert obs.hand == state.hand_of(player)

            other_cards: set[Card] = set()
            for other_seat in _ORDER:
                if other_seat != seat:
                    other_cards.update(by_seat[other_seat])
            assert not (set(obs.hand) & other_cards)


# ---------------------------------------------------------------------------
# legal_cards parity
# ---------------------------------------------------------------------------


class TestLegalCardsParity:
    def test_matches_legal_actions_by_identity_in_forced_follow(self, players):
        """North leads a heart; South holds two hearts and a club — South
        must follow suit, so only the two hearts are legal."""
        contract = Contract(ContractBid(players["N"], 100, Suit.SPADES))
        hand = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.KING),
        ]
        state = PlayState(
            contract=contract,
            players=tuple(players[s] for s in _ORDER),
            hands=tuple(tuple(hand) if s == "S" else () for s in _ORDER),
            plays=(Play(players["N"], Card(Suit.HEARTS, Rank.KING)),),
        )

        expected = state.legal_actions(players["S"])
        obs = state.observe(players["S"])

        assert obs.legal_cards == expected
        assert len(obs.legal_cards) == 2
        for offered, wanted in zip(obs.legal_cards, expected):
            assert offered is wanted


# ---------------------------------------------------------------------------
# bids passthrough
# ---------------------------------------------------------------------------


class TestBidsPassthrough:
    def test_default_is_empty_tuple(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.bids == ()

    def test_sequence_arrives_as_equal_tuple(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        bids = [PassBid(players["E"]), ContractBid(players["N"], 100, Suit.HEARTS)]

        obs = state.observe(players["N"], bids=bids)

        assert obs.bids == tuple(bids)
        assert isinstance(obs.bids, tuple)


# ---------------------------------------------------------------------------
# Derived properties
# ---------------------------------------------------------------------------


class TestDerivedProperties:
    def test_trick_number_matches_play_state_semantics(self, players):
        state = _play_first_trick(players)
        obs = state.observe(state.to_act)
        assert obs.trick_number == state.trick_number == 1

    def test_trick_number_at_start_is_zero(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.trick_number == 0

    def test_trump_suit_reflects_contract_suit(self, players):
        contract, seating, hands, _ = _deal(players, trump=Suit.CLUBS)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.trump_suit == Suit.CLUBS

    def test_trump_suit_no_trump_contract(self, players):
        contract, seating, hands, _ = _deal(players, trump=TrumpVariant.NO_TRUMP)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.trump_suit == TrumpVariant.NO_TRUMP

    def test_led_suit_none_when_trick_empty(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.led_suit is None

    def test_led_suit_is_first_card_suit(self, players):
        contract, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)
        state = state.apply(Play(players["N"], by_seat["N"][0]))
        obs = state.observe(players["E"])
        assert obs.led_suit == Suit.SPADES

    def test_played_cards_is_chronological_flatten(self, players):
        state = _play_first_trick(players)
        # Advance one more card into trick 1 so both completed_tricks and
        # current_trick are non-empty.
        winner = state.to_act
        winner_seat = next(s for s in _ORDER if players[s] is winner)
        second_card = _deal(players)[3][winner_seat][1]
        state = state.apply(Play(winner, second_card))

        obs = state.observe(winner)

        expected = tuple(play.card for trick in obs.completed_tricks for play in trick)
        expected += tuple(play.card for play in obs.current_trick)
        assert obs.played_cards == expected
        assert len(obs.played_cards) == 5

    def test_current_winner_none_when_trick_empty(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.current_winner is None

    def test_current_winner_trump_beats_lead(self, players):
        """North leads a spade; East (void, holding only hearts — the
        trump suit) ruffs and becomes the current master mid-trick.

        The winner is reported as the seat's :class:`Position`, not the
        live :class:`BasePlayer` — the observation's trick records are
        sealed to seat identifiers."""
        contract, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)
        state = state.apply(Play(players["N"], by_seat["N"][0]))
        state = state.apply(Play(players["E"], by_seat["E"][0]))

        obs = state.observe(players["S"])

        assert obs.current_winner is Position.EAST


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_assigning_any_field_raises(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])

        for field_name in _EXPECTED_FIELDS:
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(obs, field_name, None)


# ---------------------------------------------------------------------------
# Sealed trick records
# ---------------------------------------------------------------------------


class TestSealedTrickRecords:
    """The observation's trick history must carry opaque seat positions.

    ``PlayState.plays`` records hold live ``BasePlayer`` references; the
    projection must not hand those over — otherwise a strategy could reach
    through ``play.player.hand`` and read another seat's cards.
    """

    def _observed_mid_trick(self, players) -> PlayObservation:
        """One completed trick plus one play into trick 1."""
        state = _play_first_trick(players)
        winner = state.to_act
        winner_seat = next(s for s in _ORDER if players[s] is winner)
        second_card = _deal(players)[3][winner_seat][1]
        state = state.apply(Play(winner, second_card))
        return state.observe(winner)

    def test_trick_records_are_observed_plays_carrying_positions(self, players):
        obs = self._observed_mid_trick(players)
        every_play = [
            play for trick in obs.completed_tricks for play in trick
        ] + list(obs.current_trick)

        assert every_play, "the scenario must produce plays to inspect"
        for play in every_play:
            assert isinstance(play, ObservedPlay)
            assert isinstance(play.position, Position)

    def test_positions_match_the_players_who_played(self, players):
        obs = self._observed_mid_trick(players)

        # Trick 0 was played in seating order N, E, S, W.
        recorded = tuple(play.position for play in obs.completed_tricks[0])
        expected = tuple(players[s].position for s in _ORDER)
        assert recorded == expected

    def test_observed_plays_unpack_as_position_card_pairs(self, players):
        obs = self._observed_mid_trick(players)

        for position, card in obs.current_trick:
            assert isinstance(position, Position)
            assert isinstance(card, Card)

    def test_no_base_player_reachable_through_trick_records(self, players):
        obs = self._observed_mid_trick(players)
        every_play = [
            play for trick in obs.completed_tricks for play in trick
        ] + list(obs.current_trick)

        for play in every_play:
            assert not hasattr(play, "player")
            assert not any(isinstance(item, BasePlayer) for item in play)
