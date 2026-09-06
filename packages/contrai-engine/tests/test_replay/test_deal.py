"""Tests for the deal seam and its scripted implementation.

Two properties matter and they pull in opposite directions.

The **random** source must be what ``Game`` always did, so the seam is a
refactor and not a behaviour change — pinned in ``test_model/test_game.py``,
where the seeded deal is asserted against literals read off the pre-seam
code.

The **scripted** source must be the opposite of random: the dealer and
the eight cards each seat holds come off the record, and nothing about
the replay touches the RNG at all. That is what is pinned here.
"""

from __future__ import annotations

import random

import pytest
from contrai_core.card import Card
from contrai_core.position import Position
from contrai_core.types import Rank, Suit
from contrai_data import HandsDerivation, RoundRecord

from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer
from contrai_engine.replay import RoundExhaustedError, ScriptedDealSource


def _hands(offset: int) -> dict[Position, tuple[Card, ...]]:
    """Four disjoint eight-card hands, shifted by ``offset``.

    The 32 cards are laid out in a fixed order and cut into four blocks
    of eight; ``offset`` rotates which block each seat gets, so two
    rounds of the same script deal demonstrably different hands.

    Args:
        offset: How many blocks to rotate the assignment by.

    Returns:
        Seat to its eight cards.
    """

    pack = [Card(suit, rank) for suit in Suit for rank in Rank]
    seats = list(Position)
    return {
        seat: tuple(pack[((index + offset) % 4) * 8 : ((index + offset) % 4) * 8 + 8])
        for index, seat in enumerate(seats)
    }


def _round(number: int, dealer: Position, offset: int = 0) -> RoundRecord:
    """A ``RoundRecord`` carrying only what a deal source reads.

    The deal source looks at ``dealer`` and ``hands`` and nothing else,
    so the auction, tricks and score are left empty rather than filled
    with values no assertion here would read.

    Args:
        number: The round number.
        dealer: The seat that dealt.
        offset: Passed through to :func:`_hands`.

    Returns:
        The round.
    """

    return RoundRecord(
        number=number,
        dealer=dealer,
        hands=_hands(offset),
        hands_derivation=HandsDerivation.OBSERVED,
        auction=(),
        contract=None,
        tricks=(),
        derived_tricks=(),
        current_trick=(),
        belotes=(),
        score=None,
        outcome=None,
        complete=False,
    )


@pytest.fixture
def game_factory():
    """Builds a fresh four-AI game around a deal source."""

    def build(source) -> Game:
        players = [AiPlayer(seat.value, position=seat) for seat in Position]
        return Game(players, deal_source=source)

    return build


class TestScriptedDealSource:
    def test_the_dealer_comes_from_the_record(self, game_factory):
        game = game_factory(ScriptedDealSource([_round(1, Position.EAST)]))

        game.start_new_round()

        assert game.dealer.position is Position.EAST

    def test_a_dealer_the_rotation_would_never_pick_is_honoured(
        self, game_factory
    ):
        # Two rounds dealt by the *same* seat: impossible under the
        # rotation, which is precisely why the record has to be believed.
        source = ScriptedDealSource(
            [_round(1, Position.NORTH), _round(2, Position.NORTH, offset=1)]
        )
        game = game_factory(source)

        game.start_new_round()
        first = game.dealer.position
        _clear_hands(game)
        game.start_new_round()

        assert (first, game.dealer.position) == (Position.NORTH, Position.NORTH)

    def test_each_seat_is_dealt_exactly_its_recorded_hand(self, game_factory):
        scripted = _round(1, Position.EAST)
        game = game_factory(ScriptedDealSource([scripted]))

        game.start_new_round()

        for seat, cards in scripted.hands.items():
            dealt = game.players_by_position[seat].hand.cards
            assert list(dealt) == list(cards), seat

    def test_the_rounds_are_dealt_in_order(self, game_factory):
        rounds = [
            _round(1, Position.EAST, offset=0),
            _round(2, Position.SOUTH, offset=1),
        ]
        game = game_factory(ScriptedDealSource(rounds))

        game.start_new_round()
        _clear_hands(game)
        game.start_new_round()

        for seat, cards in rounds[1].hands.items():
            assert list(game.players_by_position[seat].hand.cards) == list(cards)

    def test_replaying_consumes_no_randomness(self, game_factory):
        # A replay is reproducible without a seed, which is only true if
        # nothing on the path draws. Two runs from deliberately different
        # RNG states must deal identically.
        def deal_once(seed: int) -> list[tuple]:
            random.seed(seed)
            game = game_factory(ScriptedDealSource([_round(1, Position.WEST)]))
            game.start_new_round()
            return [
                (seat, tuple(game.players_by_position[seat].hand.cards))
                for seat in Position
            ]

        assert deal_once(1) == deal_once(99)

    def test_the_dealt_hands_are_the_whole_pack(self, game_factory):
        game = game_factory(ScriptedDealSource([_round(1, Position.EAST)]))

        game.start_new_round()

        dealt = [
            card
            for seat in Position
            for card in game.players_by_position[seat].hand.cards
        ]
        assert len(dealt) == 32
        assert len(set(dealt)) == 32

    def test_running_past_the_script_is_refused(self, game_factory):
        game = game_factory(ScriptedDealSource([_round(1, Position.EAST)]))
        game.start_new_round()
        _clear_hands(game)

        with pytest.raises(RoundExhaustedError) as excinfo:
            game.start_new_round()

        assert "round 2" in str(excinfo.value)

    def test_an_empty_script_refuses_the_first_round(self, game_factory):
        game = game_factory(ScriptedDealSource([]))

        with pytest.raises(RoundExhaustedError):
            game.start_new_round()

    def test_the_rounds_are_held_as_a_tuple(self):
        rounds = [_round(1, Position.EAST)]
        source = ScriptedDealSource(rounds)

        rounds.append(_round(2, Position.SOUTH))

        assert len(source.rounds) == 1


def _clear_hands(game: Game) -> None:
    """Empty every hand, as the end of a round would.

    A scripted source hands out a fresh stacked deck each round, so the
    pile needs no refilling — but ``Deck.deal`` extends the hands it is
    given rather than replacing them, so a second deal onto unemptied
    hands would leave sixteen cards a seat.
    """

    for player in game.players:
        player.hand.clear()
