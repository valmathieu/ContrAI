"""The deal source that reads a record instead of the RNG.

:class:`~contrai_engine.model.deal.DealSource` and its random default
live in the model, because dealing is what a game does. This module adds
the other implementation: the one that hands back the dealer and the
hands a record already names, so a replayed round starts from exactly
the deal the recorded one did.

The two names from the model are re-exported here, so a caller wiring up
a replay imports all three from one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from contrai_core.deck import Deck

from ..model.deal import DealSource, RandomDealSource
from .exceptions import RoundExhaustedError

if TYPE_CHECKING:
    from contrai_data import RoundRecord

    from ..model.game import Game
    from ..model.player import Player

__all__ = ["DealSource", "RandomDealSource", "ScriptedDealSource"]


class ScriptedDealSource:
    """Deals the rounds of a record, in order, one per round started.

    Both questions the seam asks are answered straight off the record:
    the dealer is the seat ``round_dealt`` names, and the deck is the one
    :meth:`~contrai_core.Deck.stacked` builds from the recorded hands.
    No shuffle, no cut, no draw — a replay consumes no randomness at all,
    which is what makes it reproducible without a seed.

    The rounds are indexed by ``game.round_number``, the 0-based count of
    rounds already started, so the source keeps no cursor of its own and
    cannot fall out of step with the game. The corollary is that the list
    handed in must be *exactly* the rounds the controller will run, in
    order — a controller skipping a round filters it out here rather than
    skipping it later.

    Attributes:
        rounds: The rounds to deal, in the order they will be played.
    """

    def __init__(self, rounds: Sequence["RoundRecord"]) -> None:
        """Script the deals of ``rounds``.

        Args:
            rounds: The rounds to deal, in play order. Normally a
                record's own ``rounds``, filtered to those the caller
                intends to replay.
        """

        self.rounds: tuple["RoundRecord", ...] = tuple(rounds)

    def _round(self, game: "Game") -> "RoundRecord":
        """The record for the round ``game`` is about to start.

        Args:
            game: The game about to start a round.

        Returns:
            The scripted round at ``game.round_number``.

        Raises:
            RoundExhaustedError: If the game has started more rounds than
                the script holds.
        """

        index = game.round_number
        if index >= len(self.rounds):
            raise RoundExhaustedError(
                f"ScriptedDealSource: the script holds {len(self.rounds)} "
                f"round(s) to replay; the game asked for round {index + 1}"
            )
        return self.rounds[index]

    def next_dealer(self, game: "Game") -> "Player":
        """The seat the record says dealt this round.

        Args:
            game: The game about to start a round.

        Returns:
            The player sitting in the recorded dealer's seat.

        Raises:
            RoundExhaustedError: If the script has run out of rounds.
        """

        return game.players_by_position[self._round(game).dealer]

    def next_deck(self, game: "Game") -> Deck:
        """A deck stacked to deal the recorded hands.

        The record stores each hand against the seat that held it;
        :meth:`~contrai_core.Deck.deal` wants them in *deal* order. The
        game has already ordered the seats behind the dealer by the time
        this is called, so the re-ordering is a single walk of
        ``players_order`` — which is the whole reason the seam asks for
        the dealer first.

        Args:
            game: The game about to start a round, seated for it.

        Returns:
            A fresh deck that deals exactly the recorded hands.

        Raises:
            RoundExhaustedError: If the script has run out of rounds.
            InvalidCardCountError: If the recorded hands are not four
                disjoint eights — a record that projected cleanly cannot
                be, so this would mean the projection let something
                through.
        """

        hands = self._round(game).hands
        return Deck.stacked(
            [hands[player.position] for player in game.players_order]
        )
