"""Where a round's dealer and deck come from.

:class:`~contrai_engine.model.game.Game` used to answer both questions
inline: the first dealer is drawn at random and every later one is the
previous dealer's successor, and the pile is shuffled before the first
deal and cut before every later one. Both are rules of the table, and
both are exactly what a *replay* must not do — there the dealer and the
deal come from the file.

So the two questions become one seam. :class:`DealSource` is the
protocol, :class:`RandomDealSource` is today's behaviour under a name,
and it is what a ``Game`` built without one uses — so nothing about a
live game changes. ``contrai_engine.replay.deal`` supplies the other
implementation, the one that reads a record.

The protocol is deliberately two methods rather than one. A scripted
source needs the *seating* to map a record's per-seat hands into deal
order, and the seating is only known once the dealer is: ``Game`` calls
:meth:`DealSource.next_dealer`, orders the seats behind it, and only
then asks for the deck.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from contrai_core.deck import Deck

if TYPE_CHECKING:
    from .game import Game
    from .player import Player


@runtime_checkable
class DealSource(Protocol):
    """Supplies each round's dealer and the deck it is dealt from.

    Both methods are called once per round, in this order, before the
    round exists: :meth:`next_dealer` first, then — after the game has
    ordered the seats behind that dealer — :meth:`next_deck`. At both
    calls ``game.round_number`` is the 0-based index of the round about
    to start, which is what lets a scripted source index its own list
    without tracking a cursor of its own.
    """

    def next_dealer(self, game: "Game") -> "Player":
        """The seat that deals the round about to start.

        Args:
            game: The game, with ``dealer`` still holding the *previous*
                round's dealer (``None`` before the first round).

        Returns:
            The player who deals this round.
        """
        ...

    def next_deck(self, game: "Game") -> Deck:
        """The deck the round about to start is dealt from.

        Args:
            game: The game, with ``dealer`` and ``players_order`` already
                set for this round.

        Returns:
            The deck to deal. Returning ``game.deck`` after shuffling or
            cutting it in place is as valid as returning a fresh one —
            the game simply adopts whatever comes back.
        """
        ...


class RandomDealSource:
    """The canonical deal: a drawn first dealer, then cut-and-rotate.

    This is what every ``Game`` used to do inline and what one built
    without a ``deal_source`` still does, so naming it changes no
    behaviour — it only gives the behaviour a place a replay can stand
    beside.
    """

    def next_dealer(self, game: "Game") -> "Player":
        """Draw the first dealer, or pass the deal to the next seat.

        The first round's dealer is drawn at random; every later round
        hands the deal to the dealer's successor under
        ``rules.turn_direction`` — to the dealer's right when play runs
        anticlockwise (contree-domain.md §2, §4).

        Args:
            game: The game about to start a round.

        Returns:
            The dealer for this round.
        """

        if game.dealer is None:
            return random.choice(game.players)
        successor = game.dealer.position.next_in(game.rules.turn_direction)
        return game.players_by_position[successor]

    def next_deck(self, game: "Game") -> Deck:
        """Shuffle or cut the game's own pile, and hand it back.

        The collected pile is cut, not reshuffled, between rounds — the
        canonical rule (§4). The very first deal of a game has no pile to
        cut, so it always shuffles; a table running
        ``reshuffle_every_round`` shuffles before every deal instead.

        The deck is the game's own, mutated in place and returned, so its
        identity survives the round — which is what lets a caller hold a
        reference to it, or patch its ``shuffle`` / ``cut``.

        Args:
            game: The game about to start a round.

        Returns:
            ``game.deck``.
        """

        if game.round_number == 0 or game.rules.reshuffle_every_round:
            game.deck.shuffle()
        else:
            game.deck.cut()
        return game.deck
